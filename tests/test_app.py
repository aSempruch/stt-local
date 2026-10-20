from unittest.mock import Mock

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
    for loaded in (False, True):
        symbol = lambda state: app.status_symbol_name(state, model_loaded=loaded)
        assert symbol(DictationState.RECORDING_LOADING) == "waveform"
        assert symbol(DictationState.RECORDING_READY) == "waveform"
        assert symbol(DictationState.STOPPING) == "ellipsis.circle"
        assert symbol(DictationState.TRANSCRIBING) == "ellipsis.circle"
        assert symbol(DictationState.ERROR) == "exclamationmark.triangle"


def test_idle_microphone_is_hollow_until_the_model_is_loaded():
    assert app.status_symbol_name(DictationState.IDLE, model_loaded=False) == "mic"
    assert app.status_symbol_name(DictationState.IDLE, model_loaded=True) == "mic.fill"
    assert app.make_status_icon("mic") is not None


def test_audio_timeout_restarts_only_after_workflow_returns_to_idle():
    coordinator = Mock()
    coordinator.recorder.needs_restart = True
    restart = Mock()

    coordinator.state = DictationState.TRANSCRIBING
    app.restart_if_audio_unusable(
        coordinator, restart=restart, executable="/python", script="/stt-local"
    )
    restart.assert_not_called()

    coordinator.state = DictationState.IDLE
    app.restart_if_audio_unusable(
        coordinator, restart=restart, executable="/python", script="/stt-local"
    )
    restart.assert_called_once_with("/python", ["/python", "/stt-local"])


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


def test_reported_error_stays_visible_until_next_dictation():
    error = "Transcription failed: no model"

    assert app.status_display(DictationState.IDLE, error, model_loaded=True) == (
        "exclamationmark.triangle",
        error,
    )
    assert app.status_display(
        DictationState.RECORDING_LOADING, error, model_loaded=False
    ) == (
        "waveform",
        "Recording · Loading Model",
    )
    assert app.status_display(DictationState.IDLE, None, model_loaded=True) == (
        "mic.fill",
        "Idle",
    )


def make_dictation_app(tmp_path, monkeypatch):
    symbols = []
    monkeypatch.setattr(
        app, "make_status_icon", lambda name: symbols.append(name) or object()
    )
    store = ConfigStore(tmp_path / "config")
    store.save(AppConfig())
    coordinator = Mock()
    coordinator.state = DictationState.IDLE
    coordinator.recorder.needs_restart = False
    coordinator.worker.is_ready = False
    coordinator.worker.download_progress = None
    speech = Mock()
    speech.is_speaking = False
    dictation = app.DictationApp(
        coordinator=coordinator,
        settings=Mock(),
        settings_model=SettingsModel(
            store=store, registry=ProcessorRegistry(tmp_path / "processors")
        ),
        monitor=Mock(),
        speech=speech,
    )
    dictation._timer.stop()
    return dictation, coordinator, speech, symbols


def test_status_icon_fills_when_the_model_loads_and_empties_when_it_unloads(
    tmp_path, monkeypatch
):
    dictation, coordinator, _, symbols = make_dictation_app(tmp_path, monkeypatch)
    dictation.update_status(DictationState.IDLE)
    assert symbols[-1] == "mic"

    coordinator.worker.is_ready = True
    dictation._tick(None)
    assert symbols[-1] == "mic.fill"

    rendered = len(symbols)
    dictation._tick(None)
    assert len(symbols) == rendered

    coordinator.worker.is_ready = False
    dictation._tick(None)
    assert symbols[-1] == "mic"


def test_stop_speaking_is_shown_only_while_speaking(tmp_path, monkeypatch):
    dictation, _, speech, _ = make_dictation_app(tmp_path, monkeypatch)

    def shown():
        item = dictation.menu["Stop Speaking"]
        separator = dictation._speaking_separator._menuitem
        assert item._menuitem.isHidden() == separator.isHidden()
        return not item._menuitem.isHidden()

    assert not shown()

    speech.is_speaking = True
    dictation._tick(None)
    assert shown()

    # A rebuilt menu keeps showing it.
    dictation._reload_processors(None)
    assert shown()

    speech.is_speaking = False
    dictation._tick(None)
    assert not shown()
