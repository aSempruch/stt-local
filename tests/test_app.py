from pathlib import Path

from local_dictation.app import TriggerWatcher, status_presentation
from local_dictation.coordinator import DictationState


def test_existing_trigger_is_consumed_once(tmp_path):
    trigger = tmp_path / "toggle"
    trigger.touch()
    watcher = TriggerWatcher(trigger)

    assert watcher.poll()
    assert not trigger.exists()
    assert not watcher.poll()


def test_missing_trigger_is_noop(tmp_path):
    assert not TriggerWatcher(tmp_path / "missing").poll()


def test_status_presentation_covers_every_state():
    presentations = {state: status_presentation(state) for state in DictationState}

    assert presentations[DictationState.IDLE] == ("STT", "Idle")
    assert presentations[DictationState.RECORDING_LOADING][1] == "Recording · Loading Model"
    assert presentations[DictationState.RECORDING_READY][1] == "Recording · Model Ready"
    assert presentations[DictationState.TRANSCRIBING][1] == "Transcribing"
    assert set(presentations) == set(DictationState)
