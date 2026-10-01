from __future__ import annotations

import os
import sys
from typing import Any

from .alerts import menu_error_title, post_notification, report_error
from .audio import AudioRecorder
from .config import ConfigStore
from .constants import POLL_INTERVAL_SECONDS, PROCESSORS_DIR
from .coordinator import DictationCoordinator, DictationState
from .hotkey import RightCommandGestures, RightCommandMonitor
from .model_worker import WorkerManager
from .output import MacOutput, call_on_main_thread
from .overlay import DictationOverlay
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


def status_display(state: DictationState, error: str | None) -> tuple[str, str]:
    """Symbol and menu text; a reported error stays visible until the next
    dictation starts, because notifications can be missed or disabled."""
    if error is not None and state in {DictationState.IDLE, DictationState.ERROR}:
        return status_symbol_name(DictationState.ERROR), error
    return status_symbol_name(state), status_presentation(state)[1]


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


def restart_if_audio_unusable(
    coordinator: DictationCoordinator,
    *,
    restart: Any = os.execv,
    executable: str | None = None,
    script: str | None = None,
) -> None:
    if (
        coordinator.state is DictationState.IDLE
        and coordinator.recorder.needs_restart
    ):
        executable = executable or sys.executable
        script = script or sys.argv[0]
        restart(executable, [executable, script])


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
            overlay: DictationOverlay | None = None,
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
            self.overlay = overlay
            self.status_item = rumps.MenuItem("Idle")
            self._state = DictationState.IDLE
            self._error: str | None = None
            self._announced_download = False
            self._rebuild_menu()
            self._timer = rumps.Timer(self._tick, POLL_INTERVAL_SECONDS)
            self._timer.start()
            try:
                self.monitor.start()
            except Exception as exc:
                self.show_error("Keyboard monitoring unavailable", str(exc))

        def show_error(self, title: str, message: str) -> None:
            report_error(title, message)
            self._error = menu_error_title(title, message)
            self._render_status()

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
            if state not in {DictationState.IDLE, DictationState.ERROR}:
                self._error = None
            self._state = state
            self._render_status()
            if self.overlay is not None:
                self.overlay.set_state(state)

        def _render_status(self) -> None:
            symbol, status = status_display(self._state, self._error)
            self.title = status_presentation(self._state)[0]
            self._icon_nsimage = make_status_icon(symbol)
            try:
                self._nsapp.setStatusBarIcon()
            except AttributeError:
                pass
            self.status_item.title = status

        def _tick(self, _timer: Any) -> None:
            self.coordinator.refresh()
            self._show_download_progress()
            # A timed-out CoreAudio call can leave PortAudio locked in this
            # process. Replace it once transcription is finished.
            restart_if_audio_unusable(self.coordinator)

        def _show_download_progress(self) -> None:
            progress = self.coordinator.worker.download_progress
            if self.overlay is not None:
                self.overlay.set_download_progress(progress)
            if progress is None:
                self._announced_download = False
            elif not self._announced_download:
                self._announced_download = True
                post_notification(
                    "Downloading the speech model",
                    "This happens once and can take a few minutes. Your "
                    "dictation will be transcribed when it finishes.",
                )

        def _show_settings(self, _sender: Any) -> None:
            self.settings_controller.show()

        def _reload_processors(self, _sender: Any) -> None:
            self._rebuild_menu()

        def _quit(self, _sender: Any) -> None:
            self._timer.stop()
            self.monitor.stop()
            if self.overlay is not None:
                self.overlay.close()
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
    idle_override = os.environ.get("STT_LOCAL_IDLE_SECONDS")

    def idle_seconds() -> float:
        if idle_override:
            return float(idle_override)
        return settings_model.idle_unload_seconds

    app: Any = None

    def notify(title: str, message: str) -> None:
        if app is None:
            report_error(title, message)
        else:
            app.show_error(title, message)

    worker = WorkerManager(
        idle_seconds=idle_seconds,
        prompt=lambda: settings_model.bias_prompt,
    )
    settings = SettingsWindowController(
        settings_model,
        notify,
        on_idle_unload_change=worker.reschedule_idle_shutdown,
    )
    recorder = AudioRecorder()
    coordinator = DictationCoordinator(
        recorder=recorder,
        worker=worker,
        processors=registry,
        output=MacOutput(on_main=call_on_main_thread),
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
        overlay=DictationOverlay(recorder.recent_levels),
    )
    coordinator.status_callback = app.update_status
    app.update_status(DictationState.IDLE)
    return app
