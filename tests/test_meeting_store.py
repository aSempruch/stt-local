import json
from datetime import datetime, timedelta

from stt_local.meeting_store import (
    RECOVERED_NOTE,
    MeetingState,
    MeetingStore,
)

STARTED = datetime(2026, 10, 13, 14, 5, 9).astimezone()


def test_create_writes_state_and_names_folder_by_start_time(tmp_path):
    meeting = MeetingStore(tmp_path).create(STARTED)

    assert meeting.path == tmp_path / "2026-10-13 14.05.09"
    assert meeting.state is MeetingState.RECORDING
    data = json.loads((meeting.path / "meeting.json").read_text())
    assert data["state"] == "recording"
    assert datetime.fromisoformat(data["started"]) == STARTED


def test_create_adds_suffix_when_folder_exists(tmp_path):
    store = MeetingStore(tmp_path)
    first = store.create(STARTED)

    second = store.create(STARTED)

    assert second.path == tmp_path / "2026-10-13 14.05.09 2"
    assert first.path.is_dir() and second.path.is_dir()


def test_update_round_trips_notes_and_error(tmp_path):
    store = MeetingStore(tmp_path)
    meeting = store.create(STARTED)

    updated = store.update(
        meeting, state=MeetingState.FAILED, error="boom", notes=("one", "two")
    )

    assert store.load(meeting.path) == updated
    assert updated.state is MeetingState.FAILED
    assert updated.error == "boom"
    assert updated.notes == ("one", "two")
    assert updated.started == STARTED


def test_all_is_oldest_first_and_skips_unreadable(tmp_path):
    store = MeetingStore(tmp_path)
    later = store.create(STARTED + timedelta(hours=1))
    earlier = store.create(STARTED)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "meeting.json").write_text("{not json")
    (tmp_path / "stray-file").write_text("x")

    assert [meeting.path for meeting in store.all()] == [earlier.path, later.path]


def test_recover_marks_interrupted_meetings_recorded(tmp_path):
    store = MeetingStore(tmp_path)
    recording = store.create(STARTED)
    transcribing = store.update(
        store.create(STARTED + timedelta(minutes=1)), state=MeetingState.TRANSCRIBING
    )
    failed = store.update(
        store.create(STARTED + timedelta(minutes=2)),
        state=MeetingState.FAILED,
        error="boom",
    )

    recovered = store.recover()

    assert [meeting.path for meeting in recovered] == [
        recording.path,
        transcribing.path,
        failed.path,
    ]
    assert recovered[0].state is MeetingState.RECORDED
    assert recovered[0].notes == (RECOVERED_NOTE,)
    assert recovered[1].state is MeetingState.RECORDED
    assert recovered[1].notes == ()
    assert recovered[2] == failed
    assert store.load(recording.path) == recovered[0]


def test_delete_removes_folder(tmp_path):
    store = MeetingStore(tmp_path)
    meeting = store.create(STARTED)
    (meeting.path / "mic.pcm").write_bytes(b"\0\0")

    store.delete(meeting)

    assert not meeting.path.exists()
    assert store.free_bytes() > 0
