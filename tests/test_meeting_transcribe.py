import json
from datetime import datetime

import numpy as np

from stt_local import meeting_transcribe as mt
from stt_local.meeting_store import MeetingStore

SR = 16_000
STARTED = datetime(2026, 10, 13, 14, 5).astimezone()


def square(seconds, spans):
    """Spans of (start, end, amplitude); the amplitude identifies the words."""
    audio = np.zeros(int(seconds * SR), np.float32)
    for start, end, amplitude in spans:
        a, b = int(start * SR), int(end * SR)
        audio[a:b] = amplitude * np.sign(np.sin(np.arange(b - a) * 0.3) + 1e-9)
    return audio


def nonzero_regions(audio, threshold=1e-3):
    """Stands in for Silero: runs where the signal is present."""
    active = np.abs(audio) > threshold
    padded = np.concatenate([[False], active, [False]]).astype(np.int8)
    edges = np.flatnonzero(np.diff(padded))
    return [(a / SR, b / SR) for a, b in zip(edges[::2], edges[1::2])]


def energy_regions(audio, threshold_db=-40):
    """Stands in for Silero on noise-like speech: runs of 20 ms frames above
    a level, joining gaps under 300 ms."""
    n = len(audio) // 320
    levels = 10 * np.log10(np.mean(audio[: n * 320].reshape(n, 320) ** 2, axis=1) + 1e-12)
    regions = []
    for start, end in nonzero_regions(np.repeat(levels > threshold_db, 320).astype(np.float32), 0.5):
        if regions and start - regions[-1][1] < 0.3:
            regions[-1] = (regions[-1][0], end)
        else:
            regions.append((start, end))
    return [(start, end) for start, end in regions if end - start >= 0.25]


WORDS = {0.2: "Hello, can you hear me?", 0.3: "Yes, loud and clear.", 0.4: "Great, let's start."}


def fake_generate(clip, prompt, max_tokens):
    return WORDS.get(round(float(np.max(np.abs(clip))), 1), "")


def make_meeting(tmp_path, mic, system, *, notes=(), microphone="Wireless microphone"):
    store = MeetingStore(tmp_path / "meetings")
    meeting = store.create(STARTED)
    if notes:
        meeting = store.update(meeting, notes=tuple(notes))
    (meeting.path / "mic.pcm").write_bytes((mic * 32767).astype("<i2").tobytes())
    (meeting.path / "system.pcm").write_bytes((system * 32767).astype("<i2").tobytes())
    (meeting.path / "recording.json").write_text(json.dumps({"mic_device": microphone}))
    return meeting


def test_keyword_prompt_lists_keywords_first():
    assert mt.build_prompt(" Claub, Pogoria ") == (
        "Keywords: Claub, Pogoria. transcribe the speech with proper"
        " punctuation and capitalization."
    )


def test_short_clip_is_padded_and_token_cap_follows_length():
    calls = []

    def generate(clip, prompt, max_tokens):
        calls.append((len(clip), prompt, max_tokens))
        return " Hi. "

    assert mt.transcribe_segment(generate, np.ones(int(0.4 * SR), np.float32), "") == "Hi."
    assert calls == [(SR, mt.PROMPT, 36)]
    assert mt.max_tokens_for(20.0) == 232


KEYWORDS = "Claub Agent, OpenCode, Codex, Pogoria"


def test_keyword_spellings_fix_near_misses_and_keep_punctuation():
    base = "Great, so the plan is the Pagoria migration, then the club agent release."
    biased = "Great so the plan is the Pogoria migration then the Claub Agent release"

    assert mt.apply_keyword_spellings(base, biased, KEYWORDS) == (
        "Great, so the plan is the Pogoria migration, then the Claub Agent release."
    )
    assert mt.apply_keyword_spellings(
        "The open code plugin.", "The OpenCode plugin", KEYWORDS
    ) == "The OpenCode plugin."


def test_keyword_substitution_needs_a_similar_base_word():
    assert mt.apply_keyword_spellings("Hallo.", "Pogoria", KEYWORDS) == "Hallo."
    # Words the biased pass changes or adds that aren't keywords stay as the
    # punctuated pass heard them.
    assert mt.apply_keyword_spellings(
        "Morning everyone, thanks.", "Good morning everyone thanks", KEYWORDS
    ) == "Morning everyone, thanks."


def test_biased_pass_runs_only_with_keywords_and_a_near_miss():
    prompts = []

    def generate(answers):
        answers = iter(answers)

        def run(clip, prompt, max_tokens):
            prompts.append(prompt)
            return next(answers)

        return run

    clip = np.ones(SR, np.float32)
    assert mt.transcribe_segment(generate(["The Pagoria job."]), clip, "") == "The Pagoria job."
    assert prompts == [mt.PROMPT]

    prompts.clear()
    assert mt.transcribe_segment(generate(["It did."]), clip, KEYWORDS) == "It did."
    assert prompts == [mt.PROMPT]

    prompts.clear()
    result = mt.transcribe_segment(
        generate(["The Pagoria job.", "the Pogoria job"]), clip, KEYWORDS
    )
    assert result == "The Pogoria job."
    assert prompts == [mt.PROMPT, mt.build_prompt(KEYWORDS)]


def test_tidy_drops_stray_quotes():
    assert mt.tidy(' "Yes, loud and clear. ') == "Yes, loud and clear."
    assert mt.tidy('Yeah, I saw that."') == "Yeah, I saw that."
    assert mt.tidy("sure") == "sure"
    assert mt.tidy('He said "hi" there.') == 'He said "hi" there.'
    assert mt.tidy('"Quoted," she said.') == '"Quoted," she said.'
    assert mt.tidy("") == ""


def test_tidy_unwraps_quotes_around_the_whole_line():
    assert mt.tidy('"Yep, I can hear you just fine."') == "Yep, I can hear you just fine."
    assert mt.tidy('"Which migration? What do you mean?"') == "Which migration? What do you mean?"
    assert mt.tidy('"Yes," he said, "fine."') == '"Yes," he said, "fine."'


def test_keyword_similarity_counts_the_rest_of_the_keyword_phrase():
    assert mt.apply_keyword_spellings(
        "Did you try the Clive Agent release?",
        "did you try the Claub Agent release",
        KEYWORDS,
    ) == "Did you try the Claub Agent release?"
    assert mt.apply_keyword_spellings(
        "Call the travel agent.", "Call the Claub Agent", KEYWORDS
    ) == "Call the travel agent."


def test_segment_text_is_tidied():
    clip = np.ones(SR, np.float32)

    assert mt.transcribe_segment(lambda *_: '"sure.', clip, "") == "sure."


def test_plan_segments_orders_both_sides_and_gates_mic():
    system = square(10, [(1.0, 3.0, 0.2), (6.0, 8.0, 0.4)])
    leak = np.zeros_like(system)
    leak[int(0.2 * SR) :] = system[: -int(0.2 * SR)] * 0.01
    mic = square(10, [(3.5, 5.5, 0.3)]) + leak

    gated, segments = mt.plan_segments(mic, system, nonzero_regions)

    assert [(speaker, round(start, 2), round(end, 2)) for speaker, start, end in segments] == [
        ("Others", 1.0, 3.0),
        ("Me", 3.5, 5.5),
        ("Others", 6.0, 8.0),
    ]
    before_reply = slice(int(1.2 * SR), int(3.2 * SR))
    leak_db = 10 * np.log10(np.mean(leak[before_reply] ** 2))
    left_db = 10 * np.log10(np.mean(gated[before_reply] ** 2) + 1e-12)
    assert leak_db - left_db > 20


def test_plan_segments_cancels_loud_speaker_leakage_before_segmenting():
    rng = np.random.default_rng(7)
    system = np.zeros(int(12 * SR), np.float32)
    for start, end in ((1.0, 4.0), (7.0, 10.0)):
        a, b = int(start * SR), int(end * SR)
        system[a:b] = rng.normal(0, 0.1, b - a) * (0.6 + 0.4 * np.sin(np.arange(b - a) / SR * 25))
    # Laptop speakers: a long room response, loudspeaker distortion, and
    # leakage louder at the microphone than the user's own voice.
    taps = int(0.4 * SR)
    response = rng.normal(0, 1, taps) * np.exp(-np.arange(taps) / (0.08 * SR))
    response *= 10 ** (-10 / 20) / np.sqrt(np.sum(response**2))
    played = np.tanh(system * 8) / 8
    echo = np.convolve(played, response)[: len(system)].astype(np.float32)
    mine = square(12, [(4.8, 6.4, 0.05)])
    mic = mine + echo + rng.normal(0, 10 ** (-70 / 20), len(system)).astype(np.float32)

    _, segments = mt.plan_segments(mic, system, energy_regions)

    assert [(speaker, round(start, 1)) for speaker, start, _ in segments] == [
        ("Others", 1.0),
        ("Me", 4.8),
        ("Others", 7.0),
    ]


def test_transcribe_meeting_writes_ordered_markdown_and_reports_progress(tmp_path):
    system = square(10, [(1.0, 3.0, 0.2), (6.0, 8.0, 0.4)])
    mic = square(10, [(3.5, 5.5, 0.3)])
    meeting = make_meeting(tmp_path, mic, system)
    progress = []

    path = mt.transcribe_meeting(
        meeting.path,
        tmp_path / "Meetings",
        generate=fake_generate,
        speech_regions=nonzero_regions,
        keywords="",
        progress=lambda stage, fraction: progress.append((stage, fraction)),
    )

    assert path == tmp_path / "Meetings" / "2026-10-13 14.05 Meeting.md"
    text = path.read_text()
    assert "Microphone: Wireless microphone" in text
    assert text.endswith(
        "**[00:01] Others:** Hello, can you hear me?\n\n"
        "**[00:03] Me:** Yes, loud and clear.\n\n"
        "**[00:06] Others:** Great, let's start.\n"
    )
    fractions = [fraction for stage, fraction in progress if stage == "transcribe"]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0


def test_transcribe_meeting_without_speech_writes_header_only(tmp_path):
    empty = np.zeros(0, np.float32)
    meeting = make_meeting(tmp_path, empty, empty)
    progress = []

    path = mt.transcribe_meeting(
        meeting.path,
        tmp_path / "Meetings",
        generate=fake_generate,
        speech_regions=nonzero_regions,
        keywords="",
        progress=lambda stage, fraction: progress.append(fraction),
    )

    text = path.read_text()
    assert text.endswith("_No speech was detected._\n")
    assert "System audio was silent" not in text
    assert progress[-1] == 1.0


def test_transcribe_meeting_notes_when_system_track_is_silent(tmp_path):
    mic = square(10, [(3.5, 5.5, 0.3)])
    meeting = make_meeting(
        tmp_path,
        mic,
        np.zeros_like(mic),
        notes=["System audio was silent from 00:10 while other apps were playing sound."],
    )

    path = mt.transcribe_meeting(
        meeting.path,
        tmp_path / "Meetings",
        generate=fake_generate,
        speech_regions=nonzero_regions,
        keywords="",
        progress=lambda *_: None,
    )

    text = path.read_text()
    assert (
        "> System audio was silent for the whole recording. Check System Settings"
        " → Privacy & Security → Screen & System Audio Recording.\n"
    ) in text
    assert "from 00:10" not in text
    assert "**[00:03] Me:** Yes, loud and clear." in text


def test_transcribe_meeting_notes_when_system_track_never_arrived(tmp_path):
    mic = square(10, [(3.5, 5.5, 0.3)])
    meeting = make_meeting(tmp_path, mic, np.zeros(0, np.float32))

    path = mt.transcribe_meeting(
        meeting.path,
        tmp_path / "Meetings",
        generate=fake_generate,
        speech_regions=nonzero_regions,
        keywords="",
        progress=lambda *_: None,
    )

    assert mt.SILENT_SYSTEM_NOTE in path.read_text()


def test_main_reports_error_line_and_exit_code_for_unwritable_folder(tmp_path):
    mic = square(4, [(1.0, 2.0, 0.3)])
    meeting = make_meeting(tmp_path, mic, np.zeros_like(mic))
    not_a_folder = tmp_path / "file"
    not_a_folder.write_text("x")
    events = []

    code = mt.run(
        [str(meeting.path), "--output-folder", str(not_a_folder)],
        events.append,
        prepare=lambda report: report(0.5),
        load=lambda: (fake_generate, nonzero_regions),
    )

    assert code == 1
    assert events[0] == {"stage": "download", "progress": 0.5}
    assert {"stage": "load", "progress": None} in events
    assert events[-1]["stage"] == "error"
    assert "FileExistsError" in events[-1]["message"]
    assert meeting.path.is_dir()


def test_run_reports_done_with_the_transcript_path(tmp_path):
    mic = square(4, [(1.0, 2.0, 0.3)])
    meeting = make_meeting(tmp_path, mic, square(4, [(2.5, 3.5, 0.4)]))
    events = []

    code = mt.run(
        [str(meeting.path), "--output-folder", str(tmp_path / "out"), "--keywords", "Claub"],
        events.append,
        prepare=lambda report: None,
        load=lambda: (fake_generate, nonzero_regions),
    )

    assert code == 0
    assert events[-1] == {
        "stage": "done",
        "path": str(tmp_path / "out" / "2026-10-13 14.05 Meeting.md"),
    }


def test_download_only_stops_after_preparing(tmp_path):
    events = []
    loaded = []

    code = mt.run(
        ["--download-only"],
        events.append,
        prepare=lambda report: None,
        load=lambda: loaded.append(True),
    )

    assert code == 0
    assert events == [{"stage": "done", "path": None}]
    assert loaded == []


def test_model_load_failure_is_described(tmp_path):
    events = []

    def failing_prepare(report):
        raise ConnectionError("offline")

    code = mt.run(["--download-only"], events.append, prepare=failing_prepare)

    assert code == 1
    assert events[-1]["stage"] == "error"
    assert events[-1]["message"].startswith(
        "Couldn't download the meeting model ibm-granite/granite-speech-4.1-2b"
    )


def test_stdin_watch_lets_the_process_exit_cleanly():
    import subprocess
    import sys

    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from stt_local.meeting_transcribe import _exit_when_stdin_closes as w; "
            "w(); print('finished')",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        code = process.wait(timeout=20)
        output, errors = process.stdout.read(), process.stderr.read()
    finally:
        process.stdin.close()

    assert code == 0, errors
    assert output == b"finished\n"
    assert b"Fatal Python error" not in errors
