"""Meeting recordings on disk: one folder per meeting holding the helper's
audio and recording.json, plus the app's meeting.json with its state."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from .constants import MEETINGS_DIR

RECOVERED_NOTE = "Recovered after STT Local stopped during the recording."


class MeetingState(str, Enum):
    RECORDING = "recording"
    RECORDED = "recorded"
    TRANSCRIBING = "transcribing"
    FAILED = "failed"


@dataclass(frozen=True)
class Meeting:
    path: Path
    started: datetime
    state: MeetingState
    error: str | None = None
    notes: tuple[str, ...] = ()


class MeetingStore:
    def __init__(self, directory: Path = MEETINGS_DIR) -> None:
        self.directory = Path(directory)

    def create(self, started: datetime) -> Meeting:
        self.directory.mkdir(parents=True, exist_ok=True)
        name = started.strftime("%Y-%m-%d %H.%M.%S")
        path = self.directory / name
        suffix = 2
        while True:
            try:
                path.mkdir()
                break
            except FileExistsError:
                path = self.directory / f"{name} {suffix}"
                suffix += 1
        meeting = Meeting(path, started, MeetingState.RECORDING)
        self._write(meeting)
        return meeting

    def update(self, meeting: Meeting, **changes: Any) -> Meeting:
        meeting = replace(meeting, **changes)
        self._write(meeting)
        return meeting

    def load(self, path: Path) -> Meeting | None:
        try:
            data = json.loads((Path(path) / "meeting.json").read_text(encoding="utf-8"))
            return Meeting(
                Path(path),
                datetime.fromisoformat(data["started"]),
                MeetingState(data["state"]),
                data.get("error"),
                tuple(str(note) for note in data.get("notes", ())),
            )
        except (OSError, KeyError, TypeError, ValueError):
            return None

    def all(self) -> list[Meeting]:
        if not self.directory.is_dir():
            return []
        meetings = [
            meeting
            for path in self.directory.iterdir()
            if path.is_dir() and (meeting := self.load(path)) is not None
        ]
        return sorted(meetings, key=lambda meeting: (meeting.started, meeting.path.name))

    def recover(self) -> list[Meeting]:
        """Every meeting left at launch, ready to transcribe again. Nothing is
        capturing or transcribing yet, so interrupted states are stale."""
        recovered = []
        for meeting in self.all():
            if meeting.state is MeetingState.RECORDING:
                meeting = self.update(
                    meeting,
                    state=MeetingState.RECORDED,
                    notes=(*meeting.notes, RECOVERED_NOTE),
                )
            elif meeting.state is MeetingState.TRANSCRIBING:
                meeting = self.update(meeting, state=MeetingState.RECORDED)
            recovered.append(meeting)
        return recovered

    def delete(self, meeting: Meeting) -> None:
        shutil.rmtree(meeting.path, ignore_errors=True)

    def free_bytes(self) -> int:
        self.directory.mkdir(parents=True, exist_ok=True)
        return shutil.disk_usage(self.directory).free

    def _write(self, meeting: Meeting) -> None:
        data = {
            "started": meeting.started.isoformat(),
            "state": meeting.state.value,
            "error": meeting.error,
            "notes": list(meeting.notes),
        }
        path = meeting.path / "meeting.json"
        temporary = path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
