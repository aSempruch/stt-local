from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any


class MacSounds:
    def __init__(
        self,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        thread_factory: Callable[..., Any] = threading.Thread,
        start_sound: Path = Path("/System/Library/Sounds/Purr.aiff"),
        stop_sound: Path = Path("/System/Library/Sounds/Pop.aiff"),
    ) -> None:
        self._popen = popen
        self._thread_factory = thread_factory
        self._start_sound = start_sound
        self._stop_sound = stop_sound

    def play_start(self) -> None:
        self._play(self._start_sound)

    def play_stop(self) -> None:
        self._play(self._stop_sound)

    def _play(self, path: Path) -> None:
        process = self._popen(
            ["afplay", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._thread_factory(target=process.wait, daemon=True).start()
