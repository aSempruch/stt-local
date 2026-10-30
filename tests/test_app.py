from unittest.mock import Mock

from stt_local import app
from stt_local.app import status_presentation
from stt_local.config import AppConfig, ConfigStore
from stt_local.coordinator import DictationState
from stt_local.meeting_recorder import MeetingStatus
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


def test_progress_icon_is_a_template_image_for_any_fraction():
    for fraction in (0.0, 0.4, 1.0):
        image = app.make_status_icon(app.ProgressIcon(fraction))
        assert image.isTemplate()
        assert image.size().width == image.size().height == 18
        assert image.accessibilityDescription() == "STT Local"


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


def test_meeting_status_shows_while_dictation_idle_and_yields_to_dictation():
    recording = MeetingStatus("recording", "Recording Meeting · 00:05")
    transcribing = MeetingStatus("transcribing", "Transcribing Meeting · 40%", 0.4)
    downloading = MeetingStatus("downloading", "Downloading Meeting Model · 35%", 0.35)
    loading = MeetingStatus("transcribing", "Transcribing Meeting")
    display = lambda state, error, meeting: app.status_display(
        state, error, model_loaded=True, meeting=meeting
    )

    assert display(DictationState.IDLE, None, recording) == (
        "record.circle",
        "Recording Meeting · 00:05",
    )
    assert display(DictationState.IDLE, "Old error", recording)[0] == "record.circle"
    assert display(DictationState.IDLE, None, transcribing) == (
        app.ProgressIcon(0.4),
        "Transcribing Meeting · 40%",
    )
    assert display(DictationState.ERROR, None, downloading)[0] == app.ProgressIcon(0.35)
    # Before the first segment the ring is empty.
    assert display(DictationState.IDLE, None, loading)[0] == app.ProgressIcon(0.0)
    assert display(DictationState.RECORDING_READY, None, recording) == (
        "waveform",
        "Recording · Model Ready",
    )
    assert display(DictationState.IDLE, "Old error", None) == (
        "exclamationmark.triangle",
        "Old error",
    )


def test_audio_restart_waits_for_meetings():
    coordinator = Mock()
    coordinator.state = DictationState.IDLE
    coordinator.recorder.needs_restart = True
    restart = Mock()
    options = dict(restart=restart, executable="/python", script="/stt-local")

    app.restart_if_audio_unusable(coordinator, busy=lambda: True, **options)
    restart.assert_not_called()

    app.restart_if_audio_unusable(coordinator, busy=lambda: False, **options)
    restart.assert_called_once()


def make_dictation_app(tmp_path, monkeypatch, meetings=None):
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
        meetings=meetings,
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


def fake_meetings():
    meetings = Mock()
    meetings.is_recording = False
    meetings.unfinished_count = 0
    meetings.busy = False
    meetings.status.return_value = None
    return meetings


def test_meeting_menu_toggles_start_stop_and_shows_unfinished_count(tmp_path, monkeypatch):
    meetings = fake_meetings()
    dictation, _, _, symbols = make_dictation_app(tmp_path, monkeypatch, meetings)
    toggle, unfinished = dictation._meeting_item, dictation._unfinished_item

    assert toggle.title == "Start Meeting Recording"
    assert unfinished._menuitem.isHidden()
    toggle.callback(toggle)
    meetings.start_recording.assert_called_once()

    meetings.is_recording = True
    meetings.status.return_value = MeetingStatus("recording", "Recording Meeting · 00:01")
    dictation._tick(None)
    assert toggle.title == "Stop Meeting Recording"
    assert symbols[-1] == "record.circle"
    assert dictation.status_item.title == "Recording Meeting · 00:01"
    toggle.callback(toggle)
    meetings.stop_recording.assert_called_once()

    meetings.unfinished_count = 2
    dictation._tick(None)
    assert not unfinished._menuitem.isHidden()
    assert unfinished.title == "Transcribe Unfinished Recordings (2)"
    unfinished.callback(unfinished)
    meetings.transcribe_unfinished.assert_called_once()
    assert meetings.refresh.called

    # A rebuilt menu keeps the meeting state.
    dictation._reload_processors(None)
    assert dictation._meeting_item.title == "Stop Meeting Recording"
    assert not dictation._unfinished_item._menuitem.isHidden()


def test_quit_shuts_down_meetings(tmp_path, monkeypatch):
    meetings = fake_meetings()
    dictation, _, _, _ = make_dictation_app(tmp_path, monkeypatch, meetings)
    monkeypatch.setattr(app.rumps, "quit_application", Mock())

    dictation._quit(None)

    meetings.shutdown.assert_called_once()
