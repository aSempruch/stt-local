from __future__ import annotations

import os
from typing import Any

from .audio import AudioRecorder
from .config import ConfigStore
from .constants import POLL_INTERVAL_SECONDS, PROCESSORS_DIR
from .coordinator import DictationCoordinator, DictationState
from .hotkey import RightCommandGestures, RightCommandMonitor
from .model_worker import WorkerManager
from .output import MacOutput
from .processors import ProcessorRegistry
from .settings import SettingsModel, SettingsWindowController
from .sounds import MacSounds

try:
    import rumps
except ImportError:  # Allows pure model tests without installing GUI dependencies.
    rumps = None  # type: ignore[assignment]


def status_presentation(state: DictationState) -> tuple[str | None, str]:
    return {
        DictationState.IDLE: (None, "Idle"),
        DictationState.RECORDING_LOADING: (None, "Recording · Loading Model"),
        DictationState.RECORDING_READY: (None, "Recording · Model Ready"),
        DictationState.STOPPING: (None, "Stopping Recording"),
        DictationState.TRANSCRIBING: (None, "Transcribing"),
        DictationState.ERROR: (None, "Error"),
    }[state]


def status_symbol_name(state: DictationState) -> str:
    return {
        DictationState.IDLE: "mic.fill",
        DictationState.RECORDING_LOADING: "waveform",
        DictationState.RECORDING_READY: "waveform",
        DictationState.STOPPING: "ellipsis.circle",
        DictationState.TRANSCRIBING: "ellipsis.circle",
        DictationState.ERROR: "exclamationmark.triangle",
    }[state]


def make_status_icon(symbol_name: str) -> Any:
    import AppKit

    image = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        symbol_name, "STT Local"
    )
    configuration = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        16, AppKit.NSFontWeightRegular
    )
    image = image.imageWithSymbolConfiguration_(configuration)
    image.setTemplate_(True)
    return image


class ProcessorMenu:
    def __init__(self, model: SettingsModel, on_change: Any) -> None:
        self.model = model
        self.on_change = on_change

    def build_items(self) -> list[Any]:
        items = []
        for processor in self.model.refresh():
            item = rumps.MenuItem(processor.display_name, callback=self.select)
            item.processor_key = processor.key
            item.state = int(processor.key == self.model.selected_processor)
            items.append(item)
        return items

    def select(self, sender: Any) -> None:
        self.model.select(sender.processor_key)
        self.on_change()


if rumps is not None:

    class DictationApp(rumps.App):
        def __init__(
            self,
            *,
            coordinator: DictationCoordinator,
            settings: SettingsWindowController,
            settings_model: SettingsModel,
            monitor: RightCommandMonitor,
        ) -> None:
            super().__init__("STT Local", title=None, quit_button=None)
            self._icon_nsimage = make_status_icon(
                status_symbol_name(DictationState.IDLE)
            )
            self.coordinator = coordinator
            self.settings_controller = settings
            self.settings_model = settings_model
            self.processor_menu = ProcessorMenu(
                settings_model, self._rebuild_menu
            )
            self.monitor = monitor
            self.status_item = rumps.MenuItem("Idle")
            self._rebuild_menu()
            self._timer = rumps.Timer(self._tick, POLL_INTERVAL_SECONDS)
            self._timer.start()
            try:
                self.monitor.start()
            except Exception as exc:
                rumps.notification(
                    "Keyboard monitoring unavailable", "STT Local", str(exc)
                )

        def _rebuild_menu(self) -> None:
            processor_heading = rumps.MenuItem("Processor")
            self._menu.clear()
            self.menu = [
                self.status_item,
                None,
                processor_heading,
                *self.processor_menu.build_items(),
                rumps.MenuItem("Reload Processors", callback=self._reload_processors),
                None,
                rumps.MenuItem("Settings…", callback=self._show_settings),
                None,
                rumps.MenuItem("Quit STT Local", callback=self._quit),
            ]

        def update_status(self, state: DictationState) -> None:
            title, status = status_presentation(state)
            self.title = title
            self._icon_nsimage = make_status_icon(status_symbol_name(state))
            try:
                self._nsapp.setStatusBarIcon()
            except AttributeError:
                pass
            self.status_item.title = status

        def _tick(self, _timer: Any) -> None:
            self.coordinator.refresh()

        def _show_settings(self, _sender: Any) -> None:
            self.settings_controller.show()

        def _reload_processors(self, _sender: Any) -> None:
            self._rebuild_menu()

        def _quit(self, _sender: Any) -> None:
            self._timer.stop()
            self.monitor.stop()
            self.coordinator.shutdown()
            rumps.quit_application()


def build_app() -> Any:
    if rumps is None:
        raise RuntimeError("rumps is required to run STT Local")
    from PyObjCTools import AppHelper

    store = ConfigStore()
    registry = ProcessorRegistry(PROCESSORS_DIR)
    settings_model = SettingsModel(store=store, registry=registry)
    settings_model.refresh()
    configured_idle = store.load().idle_unload_seconds
    idle_seconds = float(
        os.environ.get("STT_LOCAL_IDLE_SECONDS", configured_idle)
    )

    def notify(title: str, message: str) -> None:
        rumps.notification(title, "STT Local", message)

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
    def dispatch_action(callback: Any) -> None:
        AppHelper.callAfter(callback)

    gestures = RightCommandGestures(
        state=lambda: coordinator.state,
        toggle=lambda: dispatch_action(coordinator.toggle),
        submit=lambda: dispatch_action(
            lambda: coordinator.stop_recording(submit=True)
        ),
        cancel=lambda: dispatch_action(coordinator.cancel),
    )
    app = DictationApp(
        coordinator=coordinator,
        settings=settings,
        settings_model=settings_model,
        monitor=RightCommandMonitor(gestures),
    )
    coordinator.status_callback = app.update_status
    app.update_status(DictationState.IDLE)
    return app
