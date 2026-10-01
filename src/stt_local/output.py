from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from typing import Any


def _keyboard_factory() -> Any:
    from pynput import keyboard

    return keyboard.Controller()


def _command_key() -> Any:
    from pynput import keyboard

    return keyboard.Key.cmd


def _enter_key() -> Any:
    from pynput import keyboard

    return keyboard.Key.enter


def _is_main_thread() -> bool:
    import Foundation

    return bool(Foundation.NSThread.isMainThread())


def _schedule_on_main(callback: Callable[[], None]) -> None:
    from PyObjCTools import AppHelper

    AppHelper.callAfter(callback)


def call_on_main_thread(
    callback: Callable[[], None],
    *,
    is_main: Callable[[], bool] = _is_main_thread,
    schedule: Callable[[Callable[[], None]], None] = _schedule_on_main,
    timeout: float = 5.0,
) -> None:
    """Run callback on the main thread and wait for it, re-raising its error."""
    if is_main():
        callback()
        return
    done = threading.Event()
    failure: list[BaseException] = []

    def run() -> None:
        try:
            callback()
        except BaseException as exc:
            failure.append(exc)
        finally:
            done.set()

    schedule(run)
    if not done.wait(timeout):
        raise TimeoutError("Main thread did not run the paste in time")
    if failure:
        raise failure[0]


class MacOutput:
    def __init__(
        self,
        *,
        run: Callable[..., Any] = subprocess.run,
        keyboard_factory: Callable[[], Any] = _keyboard_factory,
        command_key: Any = None,
        enter_key: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        on_main: Callable[[Callable[[], None]], None] = lambda callback: callback(),
    ) -> None:
        self._run = run
        self._keyboard_factory = keyboard_factory
        self._command_key = command_key
        self._enter_key = enter_key
        self._sleep = sleep
        self._on_main = on_main

    def send(self, text: str, *, press_enter: bool = False) -> None:
        self._run(["pbcopy"], input=text.encode(), check=True)
        self._sleep(0.05)
        # pynput reads the keyboard layout through Text Input Sources, which
        # macOS only allows on the main thread; elsewhere it aborts the app.
        self._on_main(lambda: self._type_paste(press_enter))

    def _type_paste(self, press_enter: bool) -> None:
        keyboard = self._keyboard_factory()
        command_key = self._command_key if self._command_key is not None else _command_key()
        with keyboard.pressed(command_key):
            keyboard.tap("v")
        if press_enter:
            self._sleep(0.05)
            enter_key = self._enter_key if self._enter_key is not None else _enter_key()
            keyboard.tap(enter_key)
