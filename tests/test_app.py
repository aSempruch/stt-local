from pathlib import Path

from local_dictation import app
from local_dictation.app import status_presentation
from local_dictation.coordinator import DictationState


def test_command_is_normalized_and_consumed_once(tmp_path):
    command_file = tmp_path / "stt-command"
    command_file.write_text("  SuBmIt \n")
    watcher = app.CommandWatcher(command_file)

    assert watcher.poll() == "submit"
    assert not command_file.exists()
    assert watcher.poll() is None


def test_missing_command_is_noop(tmp_path):
    assert app.CommandWatcher(tmp_path / "missing").poll() is None


def test_status_presentation_covers_every_state():
    presentations = {state: status_presentation(state) for state in DictationState}

    assert presentations[DictationState.IDLE] == (None, "Idle")
    assert presentations[DictationState.RECORDING_LOADING][1] == "Recording · Loading Model"
    assert presentations[DictationState.RECORDING_READY][1] == "Recording · Model Ready"
    assert presentations[DictationState.TRANSCRIBING][1] == "Transcribing"
    assert set(presentations) == set(DictationState)


def test_status_icon_is_native_template_microphone():
    image = app.make_status_icon()

    assert image is not None
    assert image.isTemplate()
    assert image.accessibilityDescription() == "Local Dictation"
