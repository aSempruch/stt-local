from __future__ import annotations

import subprocess
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


class MacOutput:
    def __init__(
        self,
        *,
        run: Callable[..., Any] = subprocess.run,
        keyboard_factory: Callable[[], Any] = _keyboard_factory,
        command_key: Any = None,
        enter_key: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._run = run
        self._keyboard_factory = keyboard_factory
        self._command_key = command_key
        self._enter_key = enter_key
        self._sleep = sleep

    def send(self, text: str, *, press_enter: bool = False) -> None:
        self._run(["pbcopy"], input=text.encode(), check=True)
        self._sleep(0.05)
        keyboard = self._keyboard_factory()
        command_key = self._command_key if self._command_key is not None else _command_key()
        with keyboard.pressed(command_key):
            keyboard.tap("v")
        if press_enter:
            self._sleep(0.05)
            enter_key = self._enter_key if self._enter_key is not None else _enter_key()
            keyboard.tap(enter_key)
