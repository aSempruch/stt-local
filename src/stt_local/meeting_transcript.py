"""Turn transcribed segments into the meeting's Markdown transcript."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .constants import MEETING_MODEL_LABEL


@dataclass(frozen=True)
class Line:
    start: float
    end: float
    speaker: str  # "Me" or "Others"
    text: str


def merge_lines(lines: list[Line], join_gap: float = 5.0) -> list[Line]:
    """Order lines by start, joining a line onto the previous one when the
    same person carries on within join_gap seconds."""
    merged: list[Line] = []
    for line in sorted(lines, key=lambda line: line.start):
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and previous.speaker == line.speaker
            and line.start - previous.end < join_gap
        ):
            merged[-1] = replace(
                previous,
                end=max(previous.end, line.end),
                text=f"{previous.text} {line.text}",
            )
        else:
            # Granite occasionally leaves a line's first letter lowercase;
            # pieces joined onto a line keep theirs, as they may continue a
            # sentence.
            merged.append(replace(line, text=line.text[:1].upper() + line.text[1:]))
    return merged


def format_offset(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _duration_label(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def render_markdown(
    lines: list[Line],
    *,
    started: datetime,
    duration: float,
    microphone: str | None,
    notes: list[str],
) -> str:
    details = [_duration_label(duration)]
    if microphone:
        details.append(f"Microphone: {microphone}")
    details.append(MEETING_MODEL_LABEL)
    blocks = [
        f"# Meeting · {started:%a} {started.day} {started:%b %Y, %H:%M}",
        " · ".join(details),
        *(f"> {note}" for note in notes),
    ]
    if lines:
        blocks += [
            f"**[{format_offset(line.start)}] {line.speaker}:** {line.text}"
            for line in lines
        ]
    else:
        blocks.append("_No speech was detected._")
    return "\n\n".join(blocks) + "\n"


def transcript_path(folder: Path, started: datetime) -> Path:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{started:%Y-%m-%d %H.%M} Meeting"
    path = folder / f"{name}.md"
    suffix = 2
    while path.exists():
        path = folder / f"{name} {suffix}.md"
        suffix += 1
    return path
