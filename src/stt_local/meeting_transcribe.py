"""One-shot process that turns a recorded meeting into a Markdown transcript.

Run as ``python -m stt_local.meeting_transcribe <meeting dir> --output-folder
<folder> [--keywords <text>]``, or with ``--download-only`` to fetch the models.
It reports JSON lines on stdout ({"stage": "download"|"load"|"transcribe",
"progress": ...}, then "done" or "error") and exits when its stdin pipe closes,
so it never outlives the app. A separate process returns the model's memory,
up to 8 GB, to the system when it exits.
"""

from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import re
import stat
import sys
import threading
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from .constants import (
    DEFAULT_MEETINGS_FOLDER,
    MEETING_MODEL,
    MEETING_MODEL_REVISION,
    SAMPLE_RATE,
    VAD_MODEL,
)
from .meeting_audio import (
    Region,
    apply_mask,
    cut_at_interjections,
    leakage_mask,
    split_long_regions,
)
from .meeting_store import MeetingStore
from .meeting_transcript import Line, merge_lines, render_markdown, transcript_path
from .model_worker import describe_load_error, download_model, use_system_certificates

PROMPT = "transcribe the speech with proper punctuation and capitalization."
SILENT_SYSTEM_NOTE = (
    "System audio was silent for the whole recording. Check System Settings "
    "→ Privacy & Security → Screen & System Audio Recording."
)

Generate = Callable[[np.ndarray, str, int], str]
SpeechRegions = Callable[[np.ndarray], list[Region]]
Segment = tuple[str, float, float]  # speaker, start, end in seconds


def build_prompt(keywords: str) -> str:
    """The keyword-biased prompt. Granite drops much of its punctuation with
    any keyword prompt, so only keyword spellings are taken from its output."""
    return f"Keywords: {keywords.strip()}. {PROMPT}"


def max_tokens_for(seconds: float) -> int:
    """Room for fast speech; stops a runaway repetition loop."""
    return 32 + math.ceil(10 * seconds)


_EDGES = re.compile(r"^([^\w']*)(.*?)([^\w']*)$", re.DOTALL)
SIMILAR = 0.6


def _split_token(token: str) -> tuple[str, str, str]:
    lead, core, trail = _EDGES.match(token).groups()
    return lead, core, trail


def _similar(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a, b).ratio() >= SIMILAR


def _keyword_words(keywords: str) -> set[str]:
    return set(re.findall(r"[\w']+", keywords))


def has_near_keyword(text: str, keywords: str) -> bool:
    """Whether any word, or pair of adjacent words, sounds like a keyword."""
    targets = {word.lower() for word in _keyword_words(keywords)}
    targets |= {phrase.strip().lower() for phrase in keywords.split(",") if phrase.strip()}
    words = [word.lower() for word in re.findall(r"[\w']+", text)]
    candidates = set(words)
    for first, second in zip(words, words[1:]):
        candidates |= {f"{first} {second}", first + second}
    return any(_similar(candidate, target) for candidate in candidates for target in targets)


def apply_keyword_spellings(base: str, biased: str, keywords: str) -> str:
    """Take keyword spellings from the biased transcript into the punctuated
    one, where the two line up and the original word sounds alike."""
    vocabulary = _keyword_words(keywords)
    base_tokens, biased_tokens = base.split(), biased.split()
    base_core = [_split_token(token)[1].lower() for token in base_tokens]
    biased_bare = [_split_token(token)[1] for token in biased_tokens]
    biased_core = [word.lower() for word in biased_bare]
    out: list[str] = []
    matcher = difflib.SequenceMatcher(None, base_core, biased_core, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                lead, _, trail = _split_token(base_tokens[i])
                if biased_bare[j] in vocabulary:
                    out.append(f"{lead}{biased_bare[j]}{trail}")
                else:
                    out.append(base_tokens[i])
        elif (
            op == "replace"
            and all(word in vocabulary for word in biased_bare[j1:j2])
            and _similar(" ".join(base_core[i1:i2]), " ".join(biased_core[j1:j2]))
        ):
            replacement = list(biased_bare[j1:j2])
            replacement[0] = _split_token(base_tokens[i1])[0] + replacement[0]
            replacement[-1] += _split_token(base_tokens[i2 - 1])[2]
            out += replacement
        else:
            out += base_tokens[i1:i2]
    return " ".join(out)


def tidy(text: str) -> str:
    """Drop the unbalanced quote Granite sometimes puts at either end, and
    capitalize the first letter, which it occasionally leaves lowercase."""
    text = text.strip()
    if text.count('"') % 2:
        if text.startswith('"'):
            text = text[1:].lstrip()
        elif text.endswith('"'):
            text = text[:-1].rstrip()
    return text[:1].upper() + text[1:]


def transcribe_segment(generate: Generate, clip: np.ndarray, keywords: str) -> str:
    seconds = len(clip) / SAMPLE_RATE
    if len(clip) < SAMPLE_RATE:
        clip = np.pad(clip, (0, SAMPLE_RATE - len(clip)))
    tokens = max_tokens_for(seconds)
    text = generate(clip, PROMPT, tokens).strip()
    if keywords.strip() and has_near_keyword(text, keywords):
        biased = generate(clip, build_prompt(keywords), tokens).strip()
        text = apply_keyword_spellings(text, biased, keywords)
    return tidy(text)


def plan_segments(
    mic: np.ndarray, system: np.ndarray, speech_regions: SpeechRegions
) -> tuple[np.ndarray, list[Segment]]:
    """The gated microphone track and every segment to transcribe, in order.
    Order comes from the two separate tracks, so the model needs no
    timestamps of its own."""
    length = max(len(mic), len(system))
    if length == 0:
        return mic, []
    mic = np.pad(mic, (0, length - len(mic)))
    system = np.pad(system, (0, length - len(system)))
    gated = apply_mask(mic, leakage_mask(mic, system))
    tracks = {"Me": gated, "Others": system}
    raw = {speaker: speech_regions(audio) for speaker, audio in tracks.items()}
    segments: list[Segment] = []
    for speaker, other in (("Me", "Others"), ("Others", "Me")):
        # Interjections come from the other side's own pauses, before long
        # regions are split, so split points aren't mistaken for replies.
        regions = cut_at_interjections(raw[speaker], raw[other], tracks[speaker])
        regions = split_long_regions(regions, tracks[speaker])
        segments += [(speaker, start, end) for start, end in regions]
    segments.sort(key=lambda segment: (segment[1], segment[0]))
    return gated, segments


def read_track(path: Path) -> np.ndarray:
    if not path.exists():
        return np.zeros(0, np.float32)
    return np.fromfile(path, dtype="<i2").astype(np.float32) / 32768.0


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def transcribe_meeting(
    meeting_dir: Path,
    output_folder: Path,
    *,
    generate: Generate,
    speech_regions: SpeechRegions,
    keywords: str,
    progress: Callable[[str, float | None], None],
) -> Path:
    meeting_dir = Path(meeting_dir)
    meeting = MeetingStore(meeting_dir.parent).load(meeting_dir)
    if meeting is None:
        raise ValueError(f"{meeting_dir} has no readable meeting.json")
    mic = read_track(meeting_dir / "mic.pcm")
    system = read_track(meeting_dir / "system.pcm")
    recording = _read_json(meeting_dir / "recording.json")
    notes = list(meeting.notes)
    if len(system) and not system.any():
        notes = [note for note in notes if not note.startswith("System audio")]
        notes.append(SILENT_SYSTEM_NOTE)

    gated, segments = plan_segments(mic, system, speech_regions)
    tracks = {"Me": gated, "Others": system}
    total = sum(end - start for _, start, end in segments)
    done = 0.0
    lines = []
    progress("transcribe", 0.0)
    for speaker, start, end in segments:
        clip = tracks[speaker][int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)]
        text = transcribe_segment(generate, clip, keywords)
        if text:
            lines.append(Line(start, end, speaker, text))
        done += end - start
        progress("transcribe", min(1.0, done / total) if total else 1.0)
    if not segments:
        progress("transcribe", 1.0)

    markdown = render_markdown(
        merge_lines(lines),
        started=meeting.started,
        duration=max(len(mic), len(system)) / SAMPLE_RATE,
        microphone=recording.get("mic_device") or None,
        notes=notes,
    )
    path = transcript_path(output_folder, meeting.started)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(markdown, encoding="utf-8")
    temporary.replace(path)
    return path


def model_cached() -> bool:
    """Whether every weight file of the pinned Granite revision is cached."""
    try:
        from huggingface_hub import snapshot_download

        folder = Path(
            snapshot_download(
                MEETING_MODEL, revision=MEETING_MODEL_REVISION, local_files_only=True
            )
        )
        index = json.loads((folder / "model.safetensors.index.json").read_text())
        return all((folder / name).exists() for name in set(index["weight_map"].values()))
    except Exception:
        return False


def prepare_models(report: Callable[[float], None]) -> None:
    download_model(MEETING_MODEL, report, revision=MEETING_MODEL_REVISION)
    download_model(VAD_MODEL, lambda _fraction: None)


def load_models() -> tuple[Generate, SpeechRegions]:
    from mlx_audio.stt.utils import load_model
    from mlx_audio.vad import load as load_vad

    model = load_model(MEETING_MODEL, revision=MEETING_MODEL_REVISION)
    vad = load_vad(VAD_MODEL)

    def generate(clip: np.ndarray, prompt: str, max_tokens: int) -> str:
        return model.generate(clip, prompt=prompt, max_tokens=max_tokens).text

    def speech_regions(audio: np.ndarray) -> list[Region]:
        if len(audio) < SAMPLE_RATE // 10:
            return []
        stamps = vad.get_speech_timestamps(
            audio,
            sample_rate=SAMPLE_RATE,
            min_silence_duration_ms=300,
            speech_pad_ms=150,
            return_seconds=True,
        )
        return [(float(stamp["start"]), float(stamp["end"])) for stamp in stamps]

    return generate, speech_regions


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m stt_local.meeting_transcribe")
    parser.add_argument("meeting_dir", nargs="?")
    parser.add_argument("--output-folder", default=DEFAULT_MEETINGS_FOLDER)
    parser.add_argument("--keywords", default="")
    parser.add_argument("--download-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.download_only and not args.meeting_dir:
        parser.error("a meeting folder is required")
    return args


def run(
    argv: list[str],
    emit: Callable[[dict[str, Any]], None],
    *,
    prepare: Callable[[Callable[[float], None]], None] = prepare_models,
    load: Callable[[], tuple[Generate, SpeechRegions]] = load_models,
) -> int:
    args = _parse(argv)
    percent = [-1]

    def report(fraction: float) -> None:
        if fraction >= 0 and int(fraction * 100) != percent[0]:
            percent[0] = int(fraction * 100)
            emit({"stage": "download", "progress": fraction})

    loading = True
    try:
        use_system_certificates()
        prepare(report)
        if args.download_only:
            emit({"stage": "done", "path": None})
            return 0
        emit({"stage": "load", "progress": None})
        generate, speech_regions = load()
        loading = False
        path = transcribe_meeting(
            Path(args.meeting_dir),
            Path(args.output_folder).expanduser(),
            generate=generate,
            speech_regions=speech_regions,
            keywords=args.keywords,
            progress=lambda stage, fraction: emit({"stage": stage, "progress": fraction}),
        )
    except Exception as exc:
        traceback.print_exc()
        if loading:
            message = describe_load_error(exc, MEETING_MODEL, "meeting model")
        else:
            message = f"{type(exc).__name__}: {exc}"
        emit({"stage": "error", "message": message})
        return 1
    emit({"stage": "done", "path": str(path)})
    return 0


def _protocol_emitter() -> Callable[[dict[str, Any]], None]:
    """Keep the real stdout for protocol lines and send everything libraries
    print there to stderr instead."""
    stream = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def emit(fields: dict[str, Any]) -> None:
        stream.write(json.dumps(fields) + "\n")
        stream.flush()

    return emit


def _exit_when_stdin_closes() -> None:
    if not stat.S_ISFIFO(os.fstat(0).st_mode):
        return  # Run by hand from a terminal.

    def watch() -> None:
        # os.read takes no interpreter-level lock, unlike sys.stdin, so this
        # blocked thread can't abort interpreter shutdown.
        while os.read(0, 4096):
            pass
        os._exit(1)

    threading.Thread(target=watch, daemon=True, name="stdin-watch").start()


def main(argv: list[str] | None = None) -> int:
    emit = _protocol_emitter()
    _exit_when_stdin_closes()
    return run(sys.argv[1:] if argv is None else argv, emit)


if __name__ == "__main__":
    raise SystemExit(main())
