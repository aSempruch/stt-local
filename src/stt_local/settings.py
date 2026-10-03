from __future__ import annotations

from dataclasses import replace
from typing import Any

from .config import AppConfig, ConfigStore
from .processors import ProcessorInfo, ProcessorRegistry


IDLE_UNLOAD_CHOICES = (60.0, 300.0, 600.0, 1800.0, 3600.0, 14400.0)


def idle_unload_label(seconds: float) -> str:
    minutes = seconds / 60
    if minutes >= 60 and minutes % 60 == 0:
        hours = int(minutes // 60)
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    if minutes == int(minutes):
        minutes = int(minutes)
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"
    return f"{int(seconds)} seconds"


def idle_unload_options(current: float) -> list[tuple[float, str]]:
    """Preset timeouts, plus a hand-edited config value so it stays visible."""
    choices = sorted({*IDLE_UNLOAD_CHOICES, float(current)})
    return [(seconds, idle_unload_label(seconds)) for seconds in choices]


def bring_to_front(application: Any, window: Any) -> None:
    """Show a window above other apps' windows and give it keyboard focus."""
    import AppKit

    # Launched from a bare Python interpreter, the app is background-only and
    # can never activate, so its windows open behind everything else.
    # Accessory apps can activate while still staying out of the Dock.
    if application.activationPolicy() == AppKit.NSApplicationActivationPolicyProhibited:
        application.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    activate = getattr(application, "activate", None)
    if activate is not None:
        activate()
    else:
        application.activateIgnoringOtherApps_(True)
    window.makeKeyAndOrderFront_(None)
    window.orderFrontRegardless()


class SettingsModel:
    def __init__(self, *, store: ConfigStore, registry: ProcessorRegistry) -> None:
        self.store = store
        self.registry = registry
        self.config = store.load()
        self.processors: list[ProcessorInfo] = []

    @property
    def selected_processor(self) -> str:
        return self.config.selected_processor

    @property
    def bias_prompt(self) -> str:
        return self.config.bias_prompt

    @property
    def idle_unload_seconds(self) -> float:
        return self.config.idle_unload_seconds

    @property
    def speech_voice(self) -> str:
        return self.config.speech_voice

    def set_idle_unload_seconds(self, seconds: float) -> None:
        seconds = float(seconds)
        if seconds <= 0:
            raise ValueError("The unload delay must be positive")
        if seconds == self.config.idle_unload_seconds:
            return
        self.config = replace(self.config, idle_unload_seconds=seconds)
        self.store.save(self.config)

    def set_bias_prompt(self, text: str) -> None:
        text = text.strip()
        if text == self.config.bias_prompt:
            return
        self.config = replace(self.config, bias_prompt=text)
        self.store.save(self.config)

    def refresh(self) -> list[ProcessorInfo]:
        self.processors = self.registry.list_processors()
        keys = {processor.key for processor in self.processors}
        if self.config.selected_processor not in keys:
            self.config = replace(self.config, selected_processor="plain_text")
            self.store.save(self.config)
        return self.processors

    def select(self, key: str) -> None:
        keys = {processor.key for processor in self.refresh()}
        if key not in keys:
            raise ValueError(f"Unknown processor: {key}")
        self.config = replace(self.config, selected_processor=key)
        self.store.save(self.config)

    def create(self, name: str) -> ProcessorInfo:
        created = self.registry.create(name)
        self.refresh()
        self.select(created.key)
        return created


class SettingsWindowController:
    """Lazily creates the AppKit window so model tests remain headless."""

    def __init__(
        self,
        model: SettingsModel,
        notify: Any,
        on_idle_unload_change: Any = lambda: None,
    ) -> None:
        self.model = model
        self.notify = notify
        self.on_idle_unload_change = on_idle_unload_change
        self._controller: Any | None = None

    def show(self) -> None:
        if self._controller is None:
            self._controller = self._make_controller()
        self._controller.refreshUI()
        self._controller.showWindow_(None)
        import AppKit

        bring_to_front(
            AppKit.NSApplication.sharedApplication(), self._controller.window()
        )

    def _make_controller(self) -> Any:
        import AppKit
        import Foundation
        import objc

        outer = self

        class Controller(AppKit.NSWindowController):
            def refreshUI(self):
                processors = outer.model.refresh()
                self.popup.removeAllItems()
                selected_index = 0
                for index, processor in enumerate(processors):
                    self.popup.addItemWithTitle_(processor.display_name)
                    self.popup.lastItem().setRepresentedObject_(processor.key)
                    if processor.key == outer.model.selected_processor:
                        selected_index = index
                self.popup.selectItemAtIndex_(selected_index)

                current = outer.model.idle_unload_seconds
                self.idle_popup.removeAllItems()
                for index, (seconds, label) in enumerate(
                    idle_unload_options(current)
                ):
                    self.idle_popup.addItemWithTitle_(label)
                    self.idle_popup.lastItem().setRepresentedObject_(seconds)
                    if seconds == current:
                        self.idle_popup.selectItemAtIndex_(index)

            @objc.IBAction
            def selectIdleUnload_(self, sender):
                seconds = float(sender.selectedItem().representedObject())
                try:
                    outer.model.set_idle_unload_seconds(seconds)
                    outer.on_idle_unload_change()
                except Exception as exc:
                    outer.notify("Unload delay save failed", str(exc))
                self.refreshUI()

            @objc.IBAction
            def selectProcessor_(self, sender):
                key = sender.selectedItem().representedObject()
                try:
                    outer.model.select(str(key))
                except Exception as exc:
                    outer.notify("Processor selection failed", str(exc))
                self.refreshUI()

            @objc.IBAction
            def saveBiasPrompt_(self, sender):
                try:
                    outer.model.set_bias_prompt(str(sender.stringValue()))
                except Exception as exc:
                    outer.notify("Bias prompt save failed", str(exc))

            def windowWillClose_(self, _notification):
                self.saveBiasPrompt_(self.bias_field)
                # Hand focus back to the previous app instead of leaving the
                # windowless menu-bar app active.
                AppKit.NSApplication.sharedApplication().hide_(None)

            @objc.IBAction
            def newProcessor_(self, _sender):
                alert = AppKit.NSAlert.alloc().init()
                alert.setMessageText_("New Python Processor")
                alert.setInformativeText_(
                    "Enter a name. The processor is trusted local Python code."
                )
                alert.addButtonWithTitle_("Create")
                alert.addButtonWithTitle_("Cancel")
                field = AppKit.NSTextField.alloc().initWithFrame_(
                    Foundation.NSMakeRect(0, 0, 280, 24)
                )
                alert.setAccessoryView_(field)
                if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
                    return
                try:
                    created = outer.model.create(str(field.stringValue()))
                    AppKit.NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_(
                        [Foundation.NSURL.fileURLWithPath_(str(created.path))]
                    )
                except Exception as exc:
                    outer.notify("Processor creation failed", str(exc))
                self.refreshUI()

            @objc.IBAction
            def reloadProcessors_(self, _sender):
                self.refreshUI()

            @objc.IBAction
            def showProcessors_(self, _sender):
                outer.model.registry.directory.mkdir(parents=True, exist_ok=True)
                url = Foundation.NSURL.fileURLWithPath_(
                    str(outer.model.registry.directory)
                )
                AppKit.NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([url])

        style = (
            AppKit.NSWindowStyleMaskTitled
            | AppKit.NSWindowStyleMaskClosable
            | AppKit.NSWindowStyleMaskMiniaturizable
        )
        window = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            Foundation.NSMakeRect(0, 0, 480, 465),
            style,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("STT Local Settings")
        window.center()
        controller = Controller.alloc().initWithWindow_(window)
        window.setDelegate_(controller)
        content = window.contentView()

        idle_label = AppKit.NSTextField.labelWithString_("Unload model after")
        idle_label.setFrame_(Foundation.NSMakeRect(24, 415, 150, 24))
        content.addSubview_(idle_label)

        idle_popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            Foundation.NSMakeRect(165, 413, 285, 28), False
        )
        idle_popup.setTarget_(controller)
        idle_popup.setAction_("selectIdleUnload:")
        content.addSubview_(idle_popup)
        controller.idle_popup = idle_popup

        idle_hint = AppKit.NSTextField.wrappingLabelWithString_(
            "Idle time before the model leaves memory. The next dictation "
            "reloads it, which takes a few seconds."
        )
        idle_hint.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        idle_hint.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        idle_hint.setFrame_(Foundation.NSMakeRect(24, 375, 426, 32))
        content.addSubview_(idle_hint)

        bias_label = AppKit.NSTextField.labelWithString_("Bias prompt")
        bias_label.setFrame_(Foundation.NSMakeRect(24, 345, 150, 24))
        content.addSubview_(bias_label)

        bias_field = AppKit.NSTextField.textFieldWithString_(
            outer.model.bias_prompt
        )
        bias_field.setFrame_(Foundation.NSMakeRect(24, 255, 426, 84))
        bias_field.setPlaceholderString_("Komodo, Hermes, Claub, Kubernetes")
        bias_field.setUsesSingleLineMode_(False)
        bias_field.cell().setWraps_(True)
        bias_field.cell().setScrollable_(False)
        bias_field.cell().setSendsActionOnEndEditing_(True)
        bias_field.setTarget_(controller)
        bias_field.setAction_("saveBiasPrompt:")
        content.addSubview_(bias_field)
        controller.bias_field = bias_field

        bias_hint = AppKit.NSTextField.wrappingLabelWithString_(
            "Names, jargon and spellings Whisper should expect. Saved when you "
            "press Return or leave the field; applies to the next recording."
        )
        bias_hint.setFont_(AppKit.NSFont.systemFontOfSize_(11))
        bias_hint.setTextColor_(AppKit.NSColor.secondaryLabelColor())
        bias_hint.setFrame_(Foundation.NSMakeRect(24, 212, 426, 36))
        content.addSubview_(bias_hint)

        label = AppKit.NSTextField.labelWithString_("Active processor")
        label.setFrame_(Foundation.NSMakeRect(24, 177, 150, 24))
        content.addSubview_(label)

        popup = AppKit.NSPopUpButton.alloc().initWithFrame_pullsDown_(
            Foundation.NSMakeRect(165, 175, 285, 28), False
        )
        popup.setTarget_(controller)
        popup.setAction_("selectProcessor:")
        content.addSubview_(popup)
        controller.popup = popup

        explanation = AppKit.NSTextField.wrappingLabelWithString_(
            "Processors run after transcription and before paste. They are trusted "
            "local Python files exporting process(text: str) -> str."
        )
        explanation.setFrame_(Foundation.NSMakeRect(24, 110, 426, 48))
        content.addSubview_(explanation)

        buttons = [
            ("New Processor…", "newProcessor:", 24, 142),
            ("Reload", "reloadProcessors:", 176, 90),
            ("Show in Finder", "showProcessors:", 274, 176),
        ]
        for title, action, x, width in buttons:
            button = AppKit.NSButton.buttonWithTitle_target_action_(
                title, controller, action
            )
            button.setFrame_(Foundation.NSMakeRect(x, 45, width, 32))
            content.addSubview_(button)

        controller.refreshUI()
        return controller
