from stt_local import app
from stt_local.app import status_presentation
from stt_local.config import AppConfig, ConfigStore
from stt_local.coordinator import DictationState
from stt_local.processors import ProcessorRegistry
from stt_local.settings import SettingsModel


def test_status_presentation_covers_every_state():
    presentations = {state: status_presentation(state) for state in DictationState}

    assert presentations[DictationState.IDLE] == (None, "Idle")
    assert presentations[DictationState.RECORDING_LOADING][1] == "Recording · Loading Model"
    assert presentations[DictationState.RECORDING_READY][1] == "Recording · Model Ready"
    assert presentations[DictationState.TRANSCRIBING][1] == "Transcribing"
    assert set(presentations) == set(DictationState)


def test_status_icon_is_native_template_microphone():
    image = app.make_status_icon("mic.fill")

    assert image is not None
    assert image.isTemplate()
    assert image.accessibilityDescription() == "STT Local"


def test_status_symbol_changes_with_workflow_state():
    assert app.status_symbol_name(DictationState.IDLE) == "mic.fill"
    assert app.status_symbol_name(DictationState.RECORDING_LOADING) == "waveform"
    assert app.status_symbol_name(DictationState.RECORDING_READY) == "waveform"
    assert app.status_symbol_name(DictationState.STOPPING) == "ellipsis.circle"
    assert app.status_symbol_name(DictationState.TRANSCRIBING) == "ellipsis.circle"
    assert app.status_symbol_name(DictationState.ERROR) == "exclamationmark.triangle"


def test_processor_menu_lists_choices_and_persists_selection(tmp_path):
    store = ConfigStore(tmp_path / "config")
    store.save(AppConfig())
    model = SettingsModel(
        store=store,
        registry=ProcessorRegistry(tmp_path / "processors"),
    )
    rebuilt = []
    menu = app.ProcessorMenu(model, lambda: rebuilt.append(True))

    items = menu.build_items()

    assert [item.title for item in items] == ["Plain Text", "Casual Discord"]
    assert [item.state for item in items] == [1, 0]

    menu.select(items[1])

    assert store.load().selected_processor == "casual_discord"
    assert rebuilt == [True]
    assert [item.state for item in menu.build_items()] == [0, 1]
