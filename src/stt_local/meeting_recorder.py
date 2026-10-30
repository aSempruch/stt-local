"""Meeting recording for the menu bar: runs the Swift recorder helper, then
queues each recording for the one-shot transcription process.

Both children speak JSON lines on stdout and stop when their stdin closes.
All state changes happen in refresh(), which the app calls on its timer, so
nothing here blocks the main thread.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import meeting_transcribe
from .constants import MEETING_MAX_SECONDS, MEETING_MIN_FREE_BYTES
from .meeting_store import Meeting, MeetingState, MeetingStore
from .meeting_transcript import format_offset

STOP_TIMEOUT_SECONDS = 5.0
READ_GRACE_SECONDS = 2.0
KILL_TIMEOUT_SECONDS = 2.0
PERMISSION_HINT = (
    "Check System Settings → Privacy & Security → Screen & System Audio Recording."
)
QUIT_NOTE = "Recording stopped when STT Local quit."


class JsonLinesProcess:
    """A child process whose stdout lines are parsed as JSON objects on a
    reader thread; anything else it prints there is ignored."""

    def __init__(self, args: list[str]) -> None:
        self._process = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE
        )
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._exited_at: float | None = None
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                self._events.put(event)

    def drain(self) -> list[dict[str, Any]]:
        events = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def close_input(self) -> None:
        try:
            if self._process.stdin is not None:
                self._process.stdin.close()
        except OSError:
            pass

    def exit_code(self) -> int | None:
        """None until the process has exited and its output is read. A
        grandchild that inherited stdout can hold the pipe open, so after a
        grace period the exit counts even without end of file."""
        code = self._process.poll()
        if code is None or not self._reader.is_alive():
            return code
        if self._exited_at is None:
            self._exited_at = time.monotonic()
        return code if time.monotonic() - self._exited_at >= READ_GRACE_SECONDS else None

    def wait(self, timeout: float) -> int | None:
        try:
            self._process.wait(timeout)
        except subprocess.TimeoutExpired:
            return None
        self._reader.join(timeout)
        return self._process.returncode

    def terminate(self) -> None:
        try:
            self._process.terminate()
        except OSError:
            pass

    def kill(self) -> None:
        try:
            self._process.kill()
        except OSError:
            pass


@dataclass(frozen=True)
class MeetingStatus:
    kind: str  # "recording", "transcribing" or "downloading"
    text: str


@dataclass
class _Recording:
    meeting: Meeting
    process: Any
    started_at: float
    started: bool = False
    stopping_since: float | None = None
    terminated_at: float | None = None
    error: str | None = None
    notified: set[str] = field(default_factory=set)


@dataclass
class _Job:
    meeting: Meeting
    process: Any
    stage: str = "load"
    progress: float | None = None
    path: str | None = None
    error: str | None = None


class MeetingController:
    def __init__(
        self,
        *,
        store: MeetingStore,
        recorder_path: Path,
        output_folder: Callable[[], Path],
        keywords: Callable[[], str],
        notify: Callable[[str, str], None],
        report_error: Callable[[str, str], None],
        exclude_pid: int,
        clock: Callable[[], float] = time.monotonic,
        spawn: Callable[[list[str]], Any] = JsonLinesProcess,
        model_cached: Callable[[], bool] = meeting_transcribe.model_cached,
        transcriber_command: list[str] | None = None,
        max_seconds: float = MEETING_MAX_SECONDS,
        min_free_bytes: int = MEETING_MIN_FREE_BYTES,
    ) -> None:
        self.store = store
        self.recorder_path = Path(recorder_path)
        self.output_folder = output_folder
        self.keywords = keywords
        self.notify = notify
        self.report_error = report_error
        self.exclude_pid = exclude_pid
        self.clock = clock
        self.spawn = spawn
        self.model_cached = model_cached
        self.transcriber_command = transcriber_command or [
            sys.executable,
            "-m",
            "stt_local.meeting_transcribe",
        ]
        self.max_seconds = max_seconds
        self.min_free_bytes = min_free_bytes
        self._recording: _Recording | None = None
        self._job: _Job | None = None
        self._queue: deque[Meeting] = deque()
        self._prefetch: Any | None = None
        self._prefetch_progress: float | None = None
        # Anything left from an earlier run waits for the user to ask, so a
        # login never starts an 8 GB job unasked.
        self._unfinished: list[Meeting] = store.recover()
        if self._unfinished:
            count = len(self._unfinished)
            self.notify(
                "Unfinished meeting recordings",
                f"{count} meeting recording{'s' if count > 1 else ''} "
                f"{'weren' if count > 1 else 'wasn'}'t transcribed. Choose "
                "Transcribe Unfinished Recordings in the STT Local menu.",
            )

    @property
    def is_recording(self) -> bool:
        return self._recording is not None

    @property
    def busy(self) -> bool:
        return bool(self._recording or self._job or self._queue or self._prefetch)

    @property
    def unfinished_count(self) -> int:
        return len(self._unfinished)

    def status(self) -> MeetingStatus | None:
        if self._recording is not None:
            elapsed = self.clock() - self._recording.started_at
            return MeetingStatus("recording", f"Recording Meeting · {format_offset(elapsed)}")
        job = self._job
        if job is not None and job.stage == "download":
            return MeetingStatus("downloading", _download_text(job.progress))
        if job is not None:
            text = "Transcribing Meeting"
            if self._queue:
                text += f" 1 of {1 + len(self._queue)}"
            if job.stage == "transcribe" and job.progress is not None:
                text += f" · {int(job.progress * 100)}%"
            return MeetingStatus("transcribing", text)
        if self._prefetch is not None:
            return MeetingStatus("downloading", _download_text(self._prefetch_progress))
        return None

    def start_recording(self) -> None:
        if self._recording is not None:
            return
        if not self.recorder_path.exists():
            self.report_error(
                "Meeting recorder missing",
                "Run scripts/install-launch-agent.sh to build it. It needs the "
                "Xcode Command Line Tools.",
            )
            return
        if self.store.free_bytes() < self.min_free_bytes:
            self.report_error(
                "Not enough disk space",
                f"Meeting recording needs {self.min_free_bytes / 1e9:.0f} GB free.",
            )
            return
        meeting = self.store.create(datetime.now().astimezone())
        try:
            process = self.spawn(
                [
                    str(self.recorder_path),
                    "--out",
                    str(meeting.path),
                    "--exclude-pid",
                    str(self.exclude_pid),
                ]
            )
        except OSError as exc:
            self.store.delete(meeting)
            self.report_error("Meeting recording failed", str(exc))
            return
        self._recording = _Recording(meeting, process, self.clock())
        if self._job is None and self._prefetch is None and not self.model_cached():
            # The 4.6 GB download overlaps the meeting instead of following it.
            self._prefetch = self.spawn([*self.transcriber_command, "--download-only"])
            self.notify(
                "Downloading the meeting model",
                "About 4.6 GB, once. It downloads while you record.",
            )

    def stop_recording(self) -> None:
        recording = self._recording
        if recording is not None and recording.stopping_since is None:
            recording.stopping_since = self.clock()
            recording.process.close_input()

    def transcribe_unfinished(self) -> None:
        queued = {meeting.path for meeting in self._queue}
        self._queue.extend(m for m in self._unfinished if m.path not in queued)
        self._unfinished = []

    def refresh(self) -> None:
        self._refresh_recording()
        self._refresh_prefetch()
        self._refresh_job()
        self._start_next_job()

    def shutdown(self) -> None:
        recording, self._recording = self._recording, None
        if recording is not None:
            recording.process.close_input()
            if recording.process.wait(KILL_TIMEOUT_SECONDS) is None:
                recording.process.terminate()
                recording.process.wait(KILL_TIMEOUT_SECONDS)
            self._absorb_events(recording)
            if recording.started:
                self._update_recording(
                    recording, state=MeetingState.RECORDED, extra_note=QUIT_NOTE
                )
            else:
                self.store.delete(recording.meeting)
        job, self._job = self._job, None
        if job is not None:
            job.process.terminate()
            job.process.wait(KILL_TIMEOUT_SECONDS)
            self.store.update(job.meeting, state=MeetingState.RECORDED)
        prefetch, self._prefetch = self._prefetch, None
        if prefetch is not None:
            prefetch.terminate()

    # Recording

    def _refresh_recording(self) -> None:
        recording = self._recording
        if recording is None:
            return
        self._absorb_events(recording)
        code = recording.process.exit_code()
        now = self.clock()
        if code is None:
            if recording.stopping_since is None:
                if now - recording.started_at >= self.max_seconds:
                    self.stop_recording()
                    self.notify(
                        "Meeting recording stopped",
                        "Recordings stop after 4 hours. This one is being transcribed.",
                    )
            elif recording.terminated_at is None:
                if now - recording.stopping_since > STOP_TIMEOUT_SECONDS:
                    recording.terminated_at = now
                    recording.process.terminate()
            elif now - recording.terminated_at > KILL_TIMEOUT_SECONDS:
                recording.process.kill()
            return
        self._recording = None
        if not recording.started:
            self.store.delete(recording.meeting)
            self.report_error(
                "Meeting recording failed",
                recording.error or f"The recorder stopped (exit code {code}).",
            )
            return
        extra_note = None
        if recording.stopping_since is None:
            extra_note = f"Recording ended early at {format_offset(now - recording.started_at)}."
            self.report_error(
                "Meeting recording ended early",
                recording.error or f"The recorder stopped (exit code {code}).",
            )
        meeting = self._update_recording(
            recording, state=MeetingState.RECORDED, extra_note=extra_note
        )
        self._queue.append(meeting)

    def _absorb_events(self, recording: _Recording) -> None:
        for event in recording.process.drain():
            kind = event.get("event")
            at = format_offset(float(event.get("at") or 0))
            if kind == "started":
                recording.started = True
            elif kind == "error":
                message = str(event.get("message") or "Unknown recorder error")
                recording.error = message
                if recording.started and message not in recording.notified:
                    recording.notified.add(message)
                    self.notify("Meeting recording problem", message)
            elif kind == "mic_changed":
                self._add_note(recording, f"Microphone switched to {event.get('device')} at {at}.")
            elif kind == "mic_stalled":
                self._add_note(recording, f"The microphone stopped delivering audio at {at}.")
                self._notify_once(
                    recording,
                    "Meeting microphone stopped",
                    "The microphone stopped delivering audio. STT Local is restarting it.",
                )
            elif kind == "system_silent":
                self._add_note(
                    recording,
                    f"System audio was silent from {at} while other apps were "
                    f"playing sound. {PERMISSION_HINT}",
                )
                self._notify_once(
                    recording,
                    "Meeting audio is silent",
                    f"Other apps are playing sound, but the recording hears none. {PERMISSION_HINT}",
                )

    def _add_note(self, recording: _Recording, note: str) -> None:
        recording.meeting = self.store.update(
            recording.meeting, notes=(*recording.meeting.notes, note)
        )

    def _notify_once(self, recording: _Recording, title: str, message: str) -> None:
        if title not in recording.notified:
            recording.notified.add(title)
            self.notify(title, message)

    def _update_recording(
        self, recording: _Recording, *, state: MeetingState, extra_note: str | None
    ) -> Meeting:
        notes = recording.meeting.notes + ((extra_note,) if extra_note else ())
        return self.store.update(recording.meeting, state=state, notes=notes)

    # Model download

    def _refresh_prefetch(self) -> None:
        prefetch = self._prefetch
        if prefetch is None:
            return
        for event in prefetch.drain():
            if event.get("stage") == "download":
                self._prefetch_progress = event.get("progress")
        if prefetch.exit_code() is not None:
            # A failed download is retried, and reported, by the next job.
            self._prefetch = None
            self._prefetch_progress = None

    # Transcription

    def _start_next_job(self) -> None:
        # Two downloads of the same files would only wait on each other.
        if self._job is not None or not self._queue or self._prefetch is not None:
            return
        meeting = self.store.load(self._queue.popleft().path)
        if meeting is None:
            return  # Its folder was deleted while it waited.
        meeting = self.store.update(meeting, state=MeetingState.TRANSCRIBING, error=None)
        args = [
            *self.transcriber_command,
            str(meeting.path),
            "--output-folder",
            str(self.output_folder()),
        ]
        keywords = self.keywords().strip()
        if keywords:
            args += ["--keywords", keywords]
        try:
            self._job = _Job(meeting, self.spawn(args))
        except OSError as exc:
            self._fail(meeting, str(exc))

    def _refresh_job(self) -> None:
        job = self._job
        if job is None:
            return
        for event in job.process.drain():
            stage = event.get("stage")
            if stage in {"download", "load", "transcribe"}:
                job.stage = stage
                job.progress = event.get("progress")
            elif stage == "done":
                job.path = event.get("path")
            elif stage == "error":
                job.error = str(event.get("message"))
        code = job.process.exit_code()
        if code is None:
            return
        self._job = None
        if job.path:
            self.store.delete(job.meeting)
            self.notify("Meeting transcript ready", Path(job.path).name)
        else:
            self._fail(job.meeting, job.error or f"The transcriber stopped (exit code {code}).")

    def _fail(self, meeting: Meeting, message: str) -> None:
        failed = self.store.update(meeting, state=MeetingState.FAILED, error=message)
        self._unfinished.append(failed)
        self.report_error("Meeting transcription failed", message)


def _download_text(progress: float | None) -> str:
    if progress is None:
        return "Downloading Meeting Model"
    return f"Downloading Meeting Model · {int(progress * 100)}%"
