from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .audio import AudioRecorder
from .config import ConfigStore
from .constants import POLL_INTERVAL_SECONDS, PROCESSORS_DIR, TRIGGER_FILE
from .coordinator import DictationCoordinator, DictationState
from .model_worker import WorkerManager
from .output import MacOutput
from .processors import ProcessorRegistry
from .settings import SettingsModel, SettingsWindowController
from .sounds import MacSounds

try:
    import rumps
except ImportError:  # Allows pure model tests without installing GUI dependencies.
    rumps = None  # type: ignore[assignment]


class TriggerWatcher:
    def __init__(self, path: Path = TRIGGER_FILE) -> None:
        self.path = Path(path)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)

    def poll(self) -> bool:
        if not self.path.exists():
            return False
        self.path.unlink(missing_ok=True)
        return True


def status_presentation(state: DictationState) -> tuple[str, str]:
    return {
        DictationState.IDLE: ("STT", "Idle"),
        DictationState.RECORDING_LOADING: ("●", "Recording · Loading Model"),
        DictationState.RECORDING_READY: ("●", "Recording · Model Ready"),
        DictationState.STOPPING: ("…", "Stopping Recording"),
        DictationState.TRANSCRIBING: ("…", "Transcribing"),
        DictationState.ERROR: ("!", "Error"),
    }[state]


if rumps is not None:

    class DictationApp(rumps.App):
        def __init__(
            self,
            *,
            coordinator: DictationCoordinator,
            settings: SettingsWindowController,
            settings_model: SettingsModel,
            watcher: TriggerWatcher | None = None,
        ) -> None:
            super().__init__("STT", quit_button=None)
            self.coordinator = coordinator
            self.settings_controller = settings
            self.settings_model = settings_model
            self.watcher = watcher or TriggerWatcher()
            self.watcher.clear()
            self.status_item = rumps.MenuItem("Idle")
            self.menu = [
                self.status_item,
                None,
                rumps.MenuItem("Settings…", callback=self._show_settings),
                rumps.MenuItem("Reload Processors", callback=self._reload_processors),
                None,
                rumps.MenuItem("Quit Local Dictation", callback=self._quit),
            ]
            self._timer = rumps.Timer(self._tick, POLL_INTERVAL_SECONDS)
            self._timer.start()

        def update_status(self, state: DictationState) -> None:
            title, status = status_presentation(state)
            self.title = title
            self.status_item.title = status

        def _tick(self, _timer: Any) -> None:
            self.coordinator.refresh()
            if self.watcher.poll():
                self.coordinator.toggle()

        def _show_settings(self, _sender: Any) -> None:
            self.settings_controller.show()

        def _reload_processors(self, _sender: Any) -> None:
            self.settings_model.refresh()

        def _quit(self, _sender: Any) -> None:
            self._timer.stop()
            self.coordinator.shutdown()
            rumps.quit_application()


def build_app() -> Any:
    if rumps is None:
        raise RuntimeError("rumps is required to run Local Dictation")
    from PyObjCTools import AppHelper

    store = ConfigStore()
    registry = ProcessorRegistry(PROCESSORS_DIR)
    settings_model = SettingsModel(store=store, registry=registry)
    settings_model.refresh()
    configured_idle = store.load().idle_unload_seconds
    idle_seconds = float(
        os.environ.get("LOCAL_DICTATION_IDLE_SECONDS", configured_idle)
    )

    def notify(title: str, message: str) -> None:
        rumps.notification(title, "Local Dictation", message)

    settings = SettingsWindowController(settings_model, notify)
    coordinator = DictationCoordinator(
        recorder=AudioRecorder(),
        worker=WorkerManager(idle_seconds=idle_seconds),
        processors=registry,
        output=MacOutput(),
        sounds=MacSounds(),
        selected_processor=lambda: settings_model.selected_processor,
        notification_callback=notify,
        dispatch=lambda callback: AppHelper.callAfter(callback),
    )
    app = DictationApp(
        coordinator=coordinator,
        settings=settings,
        settings_model=settings_model,
    )
    coordinator.status_callback = app.update_status
    app.update_status(DictationState.IDLE)
    return app
