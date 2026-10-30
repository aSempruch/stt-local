import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from stt_local.meeting_recorder import JsonLinesProcess, MeetingController, MeetingStatus
from stt_local.meeting_store import RECOVERED_NOTE, MeetingState, MeetingStore

FAKE_HELPER = f"""#!{sys.executable}
import json, os, sys, time
mode = os.environ.get("FAKE_HELPER_MODE", "normal")
out = sys.argv[sys.argv.index("--out") + 1]
def say(**fields):
    print(json.dumps(fields), flush=True)
if mode == "fail":
    say(event="error", message="No microphone is available.")
    sys.exit(1)
for name in ("mic", "system"):
    with open(os.path.join(out, name + ".pcm"), "wb") as f:
        f.write(b"\\x01\\x00" * 1600)
say(event="started", system_device="Speakers", mic_device="Wireless microphone", mic_rate=48000)
if mode == "crash":
    time.sleep(0.1)
    say(event="error", message="Core Audio stopped responding.")
    sys.exit(3)
if mode == "events":
    say(event="mic_changed", device="MacBook Pro Microphone", at=754.2)
    say(event="mic_stalled", at=800.0)
    say(event="mic_stalled", at=900.0)
    say(event="system_silent", at=10.0)
if mode == "stubborn":
    while True:
        time.sleep(1)
sys.stdin.read()
with open(os.path.join(out, "recording.json"), "w") as f:
    json.dump(dict(mic_device="Wireless microphone"), f)
"""

FAKE_TRANSCRIBER = """import json, os, sys, time
from pathlib import Path
mode = os.environ.get("FAKE_TRANSCRIBER_MODE", "ok")
def say(**fields):
    print(json.dumps(fields), flush=True)
args = sys.argv[1:]
if "--download-only" in args:
    say(stage="download", progress=0.35)
    sys.stdin.read()
    sys.exit(0)
log = os.environ.get("FAKE_TRANSCRIBER_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(args) + "\\n")
if mode == "fail":
    say(stage="error", message="Couldn't load the meeting model.")
    sys.exit(1)
if mode == "die":
    sys.exit(9)
say(stage="load", progress=None)
say(stage="transcribe", progress=0.4)
if os.environ.get("FAKE_TRANSCRIBER_HOLD"):
    while not os.path.exists(os.environ["FAKE_TRANSCRIBER_HOLD"]):
        time.sleep(0.02)
folder = Path(args[args.index("--output-folder") + 1])
folder.mkdir(parents=True, exist_ok=True)
path = folder / (Path(args[0]).name + " Meeting.md")
path.write_text("# Meeting\\n")
say(stage="done", path=str(path))
"""


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def setup(tmp_path, monkeypatch):
    helper = tmp_path / "fake-helper"
    helper.write_text(FAKE_HELPER)
    helper.chmod(0o755)
    transcriber = tmp_path / "fake_transcriber.py"
    transcriber.write_text(FAKE_TRANSCRIBER)
    monkeypatch.setenv("FAKE_TRANSCRIBER_LOG", str(tmp_path / "transcriber-calls"))
    notes, errors = [], []
    clock = Clock()

    def make(**overrides):
        options = dict(
            store=MeetingStore(tmp_path / "meetings"),
            recorder_path=helper,
            output_folder=lambda: tmp_path / "Meetings",
            keywords=lambda: "Claub, Pogoria",
            notify=lambda title, message: notes.append((title, message)),
            report_error=lambda title, message: errors.append((title, message)),
            exclude_pid=4242,
            clock=clock,
            transcriber_command=[sys.executable, str(transcriber)],
            model_cached=lambda: True,
        )
        options.update(overrides)
        return MeetingController(**options)

    return make, notes, errors, clock, tmp_path


def pump(controller, until, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        controller.refresh()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for the controller")


def folders(tmp_path):
    root = tmp_path / "meetings"
    return sorted(path for path in root.iterdir() if path.is_dir()) if root.exists() else []


def recording_started(controller):
    return controller.status() is not None and controller._recording.started


def test_start_and_stop_records_then_queues_transcription_and_deletes_folder_on_done(setup):
    make, notes, errors, clock, tmp_path = setup
    controller = make()

    controller.start_recording()
    pump(controller, lambda: recording_started(controller))
    assert controller.is_recording
    clock.now += 754
    assert controller.status() == MeetingStatus("recording", "Recording Meeting · 12:34")
    [meeting_dir] = folders(tmp_path)

    controller.stop_recording()
    pump(controller, lambda: not controller.busy)

    assert not meeting_dir.exists()
    transcript = tmp_path / "Meetings" / f"{meeting_dir.name} Meeting.md"
    assert transcript.exists()
    assert notes == [("Meeting transcript ready", transcript.name)]
    assert errors == []
    [call] = [json.loads(line) for line in (tmp_path / "transcriber-calls").read_text().splitlines()]
    assert call == [
        str(meeting_dir),
        "--output-folder",
        str(tmp_path / "Meetings"),
        "--keywords",
        "Claub, Pogoria",
    ]


def test_start_fails_cleanly_when_helper_reports_error_before_started(setup, monkeypatch):
    make, notes, errors, clock, tmp_path = setup
    monkeypatch.setenv("FAKE_HELPER_MODE", "fail")
    controller = make()

    controller.start_recording()
    pump(controller, lambda: not controller.is_recording)

    assert errors == [("Meeting recording failed", "No microphone is available.")]
    assert folders(tmp_path) == []
    assert controller.status() is None


def test_start_refuses_when_recorder_missing_or_disk_low(setup):
    make, notes, errors, clock, tmp_path = setup

    make(recorder_path=tmp_path / "missing").start_recording()
    low = make(min_free_bytes=10**18)
    low.start_recording()

    assert [title for title, _ in errors] == ["Meeting recorder missing", "Not enough disk space"]
    assert "install-launch-agent.sh" in errors[0][1]
    assert not low.is_recording
    assert folders(tmp_path) == []


def test_helper_exit_mid_recording_keeps_audio_and_notes_early_end(setup, monkeypatch):
    make, notes, errors, clock, tmp_path = setup
    monkeypatch.setenv("FAKE_HELPER_MODE", "crash")
    monkeypatch.setenv("FAKE_TRANSCRIBER_MODE", "fail")
    controller = make()

    controller.start_recording()
    pump(controller, lambda: controller._recording is not None and controller._recording.started)
    clock.now += 65
    pump(controller, lambda: not controller.is_recording)
    pump(controller, lambda: not controller.busy)

    [meeting] = MeetingStore(tmp_path / "meetings").all()
    assert "Recording ended early at 01:05." in meeting.notes
    assert (meeting.path / "mic.pcm").exists()
    assert errors[0] == ("Meeting recording ended early", "Core Audio stopped responding.")


def test_helper_events_become_notes_and_one_notification_each(setup, monkeypatch):
    make, notes, errors, clock, tmp_path = setup
    monkeypatch.setenv("FAKE_HELPER_MODE", "events")
    controller = make()

    controller.start_recording()
    [meeting_dir] = folders(tmp_path)
    store = MeetingStore(tmp_path / "meetings")
    pump(controller, lambda: len(store.load(meeting_dir).notes) == 4)

    assert store.load(meeting_dir).notes == (
        "Microphone switched to MacBook Pro Microphone at 12:34.",
        "The microphone stopped delivering audio at 13:20.",
        "The microphone stopped delivering audio at 15:00.",
        "System audio was silent from 00:10 while other apps were playing sound."
        " Check System Settings → Privacy & Security → Screen & System Audio Recording.",
    )
    assert [title for title, _ in notes] == [
        "Meeting microphone stopped",
        "Meeting audio is silent",
    ]
    controller.shutdown()


def test_recording_stops_after_four_hours(setup):
    make, notes, errors, clock, tmp_path = setup
    controller = make()
    controller.start_recording()
    pump(controller, lambda: recording_started(controller))

    clock.now += 4 * 3600
    pump(controller, lambda: not controller.is_recording)

    assert notes[0][0] == "Meeting recording stopped"
    assert "4 hours" in notes[0][1]
    pump(controller, lambda: not controller.busy)


def test_stop_terminates_helper_that_ignores_stdin_after_five_seconds(setup, monkeypatch):
    make, notes, errors, clock, tmp_path = setup
    monkeypatch.setenv("FAKE_HELPER_MODE", "stubborn")
    controller = make()
    controller.start_recording()
    pump(controller, lambda: recording_started(controller))

    controller.stop_recording()
    for _ in range(10):
        controller.refresh()
        time.sleep(0.02)
    assert controller.is_recording

    clock.now += 6
    pump(controller, lambda: not controller.is_recording)
    pump(controller, lambda: not controller.busy)
    assert errors == []


def test_transcriber_failure_marks_failed_and_retry_succeeds(setup, monkeypatch):
    make, notes, errors, clock, tmp_path = setup
    monkeypatch.setenv("FAKE_TRANSCRIBER_MODE", "fail")
    controller = make()
    controller.start_recording()
    pump(controller, lambda: recording_started(controller))
    controller.stop_recording()
    pump(controller, lambda: not controller.busy)

    [meeting] = MeetingStore(tmp_path / "meetings").all()
    assert meeting.state is MeetingState.FAILED
    assert meeting.error == "Couldn't load the meeting model."
    assert errors == [("Meeting transcription failed", "Couldn't load the meeting model.")]
    assert controller.unfinished_count == 1

    monkeypatch.setenv("FAKE_TRANSCRIBER_MODE", "ok")
    controller.transcribe_unfinished()
    assert controller.unfinished_count == 0
    pump(controller, lambda: not controller.busy)
    assert folders(tmp_path) == []
    assert notes[-1][0] == "Meeting transcript ready"


def test_transcriber_dying_without_a_message_is_reported(setup, monkeypatch):
    make, notes, errors, clock, tmp_path = setup
    monkeypatch.setenv("FAKE_TRANSCRIBER_MODE", "die")
    controller = make()
    controller.start_recording()
    pump(controller, lambda: recording_started(controller))
    controller.stop_recording()
    pump(controller, lambda: not controller.busy)

    assert errors == [("Meeting transcription failed", "The transcriber stopped (exit code 9).")]


def test_queue_runs_one_job_at_a_time_and_status_counts_them(setup, monkeypatch):
    make, notes, errors, clock, tmp_path = setup
    hold = tmp_path / "release"
    monkeypatch.setenv("FAKE_TRANSCRIBER_HOLD", str(hold))
    controller = make()
    for _ in range(2):
        controller.start_recording()
        pump(controller, lambda: recording_started(controller))
        controller.stop_recording()
        pump(controller, lambda: not controller.is_recording)

    pump(controller, lambda: controller.status() is not None and "40%" in controller.status().text)
    assert controller.status() == MeetingStatus("transcribing", "Transcribing Meeting 1 of 2 · 40%")
    assert len(json_lines(tmp_path / "transcriber-calls")) == 1

    hold.write_text("go")
    pump(controller, lambda: not controller.busy)
    assert len(json_lines(tmp_path / "transcriber-calls")) == 2
    assert [title for title, _ in notes] == ["Meeting transcript ready"] * 2


def json_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_unfinished_meetings_found_at_launch_notify_once_and_wait_for_click(setup):
    make, notes, errors, clock, tmp_path = setup
    store = MeetingStore(tmp_path / "meetings")
    interrupted = store.create(datetime(2026, 10, 13, 14, 5).astimezone())
    failed = store.update(
        store.create(datetime(2026, 10, 14, 9, 0).astimezone()),
        state=MeetingState.FAILED,
        error="boom",
    )

    controller = make()
    for _ in range(5):
        controller.refresh()
        time.sleep(0.02)

    assert controller.unfinished_count == 2
    assert not controller.busy
    assert notes == [
        (
            "Unfinished meeting recordings",
            "2 meeting recordings weren't transcribed. Choose Transcribe "
            "Unfinished Recordings in the STT Local menu.",
        )
    ]
    assert store.load(interrupted.path).notes == (RECOVERED_NOTE,)

    controller.transcribe_unfinished()
    pump(controller, lambda: not controller.busy)
    assert folders(tmp_path) == []
    assert failed.path.name in (tmp_path / "transcriber-calls").read_text()


def test_prefetches_model_at_start_when_not_cached(setup):
    make, notes, errors, clock, tmp_path = setup
    controller = make(model_cached=lambda: False)

    controller.start_recording()
    pump(controller, lambda: recording_started(controller))

    assert notes[0][0] == "Downloading the meeting model"
    controller.stop_recording()
    pump(controller, lambda: not controller.is_recording)
    pump(
        controller,
        lambda: controller.status() == MeetingStatus("downloading", "Downloading Meeting Model · 35%"),
    )
    # The transcription waits for the download to finish.
    assert not (tmp_path / "transcriber-calls").exists()
    controller.shutdown()


def test_busy_while_recording_or_transcribing(setup, monkeypatch):
    make, notes, errors, clock, tmp_path = setup
    hold = tmp_path / "release"
    monkeypatch.setenv("FAKE_TRANSCRIBER_HOLD", str(hold))
    controller = make()
    assert not controller.busy

    controller.start_recording()
    assert controller.busy
    pump(controller, lambda: recording_started(controller))
    controller.stop_recording()
    pump(controller, lambda: not controller.is_recording)
    assert controller.busy

    hold.write_text("go")
    pump(controller, lambda: not controller.busy)


def test_shutdown_stops_helper_and_transcriber_and_leaves_meetings_unfinished(setup, monkeypatch):
    make, notes, errors, clock, tmp_path = setup
    hold = tmp_path / "release"
    monkeypatch.setenv("FAKE_TRANSCRIBER_HOLD", str(hold))
    controller = make()
    controller.start_recording()
    pump(controller, lambda: recording_started(controller))
    controller.stop_recording()
    pump(controller, lambda: controller.status() is not None and controller.status().kind == "transcribing")
    controller.start_recording()
    pump(controller, lambda: recording_started(controller))

    controller.shutdown()

    meetings = MeetingStore(tmp_path / "meetings").all()
    assert [meeting.state for meeting in meetings] == [MeetingState.RECORDED] * 2
    assert meetings[1].notes == ("Recording stopped when STT Local quit.",)
    assert (meetings[1].path / "recording.json").exists()


def test_json_lines_process_skips_lines_that_are_not_json(tmp_path):
    script = tmp_path / "talk.py"
    script.write_text('print("hello"); print(\'{"stage": "done"}\'); print("[1, 2]")')

    process = JsonLinesProcess([sys.executable, str(script)])
    deadline = time.monotonic() + 10
    while process.exit_code() is None and time.monotonic() < deadline:
        time.sleep(0.02)

    assert process.exit_code() == 0
    assert process.drain() == [{"stage": "done"}]
