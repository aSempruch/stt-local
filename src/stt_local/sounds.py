from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any


SOUND_ASSETS = Path(__file__).with_name("sound_assets")


def _load_nssound(path: Path) -> Any:
    import AppKit

    # byReference=False reads the samples into memory now, so play() starts
    # without disk access. NSSound only holds the output device while a cue
    # is playing and does not register as a Now Playing media session.
    return AppKit.NSSound.alloc().initWithContentsOfFile_byReference_(
        str(path), False
    )


class MacSounds:
    """Short cues played in-process. Spawning afplay took about 200 ms before
    a cue became audible; a preloaded NSSound starts in a fraction of that."""

    def __init__(
        self,
        *,
        load: Callable[[Path], Any] = _load_nssound,
        start_sound: Path = SOUND_ASSETS / "start-recording.wav",
        stop_sound: Path = Path("/System/Library/Sounds/Ping.aiff"),
        cancel_sound: Path = Path("/System/Library/Sounds/Pop.aiff"),
        submit_sound: Path = SOUND_ASSETS / "submit.wav",
    ) -> None:
        self._start_sound = load(start_sound)
        self._stop_sound = load(stop_sound)
        self._cancel_sound = load(cancel_sound)
        self._submit_sound = load(submit_sound)

    def play_start(self) -> None:
        self._play(self._start_sound)

    def play_stop(self) -> None:
        self._play(self._stop_sound)

    def play_cancel(self) -> None:
        self._play(self._cancel_sound)

    def play_submit(self) -> None:
        self._play(self._submit_sound)

    def _play(self, sound: Any) -> None:
        if sound is None:
            return
        # play() is ignored while the same sound is still playing, so restart it.
        sound.stop()
        sound.play()
