from __future__ import annotations

from dataclasses import replace
from typing import Any

from .config import AppConfig, ConfigStore
from .processors import ProcessorInfo, ProcessorRegistry


class SettingsModel:
    def __init__(self, *, store: ConfigStore, registry: ProcessorRegistry) -> None:
        self.store = store
        self.registry = registry
        self.config = store.load()
        self.processors: list[ProcessorInfo] = []

    @property
    def selected_processor(self) -> str:
        return self.config.selected_processor

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
    ) -> None:
        self.model = model
        self.notify = notify
        self._controller: Any | None = None

    def show(self) -> None:
        if self._controller is None:
            self._controller = self._make_controller()
        self._controller.refreshUI()
        self._controller.showWindow_(None)
        self._controller.window().makeKeyAndOrderFront_(None)

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

            @objc.IBAction
            def selectProcessor_(self, sender):
                key = sender.selectedItem().representedObject()
                try:
                    outer.model.select(str(key))
                except Exception as exc:
                    outer.notify("Processor selection failed", str(exc))
                self.refreshUI()

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
            Foundation.NSMakeRect(0, 0, 480, 235),
            style,
            AppKit.NSBackingStoreBuffered,
            False,
        )
        window.setTitle_("STT Local Settings")
        window.center()
        controller = Controller.alloc().initWithWindow_(window)
        content = window.contentView()

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
