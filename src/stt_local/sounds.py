from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any


SOUND_ASSETS = Path(__file__).with_name("sound_assets")


class MacSounds:
    def __init__(
        self,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        thread_factory: Callable[..., Any] = threading.Thread,
        start_sound: Path = SOUND_ASSETS / "start-recording.wav",
        stop_sound: Path = Path("/System/Library/Sounds/Ping.aiff"),
        cancel_sound: Path = Path("/System/Library/Sounds/Pop.aiff"),
        submit_sound: Path = SOUND_ASSETS / "submit.wav",
    ) -> None:
        self._popen = popen
        self._thread_factory = thread_factory
        self._start_sound = start_sound
        self._stop_sound = stop_sound
        self._cancel_sound = cancel_sound
        self._submit_sound = submit_sound

    def play_start(self) -> None:
        self._play(self._start_sound)

    def play_stop(self) -> None:
        self._play(self._stop_sound)

    def play_cancel(self) -> None:
        self._play(self._cancel_sound)

    def play_submit(self) -> None:
        self._play(self._submit_sound)

    def _play(self, path: Path) -> None:
        process = self._popen(
            ["afplay", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._thread_factory(target=process.wait, daemon=True).start()
