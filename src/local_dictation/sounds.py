from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any


class MacSounds:
    def __init__(
        self,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        start_sound: Path = Path("/System/Library/Sounds/Tink.aiff"),
        stop_sound: Path = Path("/System/Library/Sounds/Pop.aiff"),
    ) -> None:
        self._popen = popen
        self._start_sound = start_sound
        self._stop_sound = stop_sound

    def play_start(self) -> None:
        self._play(self._start_sound)

    def play_stop(self) -> None:
        self._play(self._stop_sound)

    def _play(self, path: Path) -> None:
        self._popen(
            ["afplay", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
