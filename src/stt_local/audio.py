from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from .constants import SAMPLE_RATE


def trim_trailing_silence(
    audio: np.ndarray, sample_rate: int, pad_seconds: float = 0.3
) -> np.ndarray:
    if audio.size == 0:
        return audio
    threshold = max(float(np.max(np.abs(audio))) * 0.02, 1e-4)
    above = np.flatnonzero(np.abs(audio) > threshold)
    if above.size == 0:
        return audio
    cutoff = min(len(audio), int(above[-1]) + 1 + int(pad_seconds * sample_rate))
    return audio[:cutoff]


def _refresh_sounddevice() -> None:
    import sounddevice as sd

    sd._terminate()
    sd._initialize()


def _make_input_stream(**kwargs: Any) -> Any:
    import sounddevice as sd

    return sd.InputStream(**kwargs)


class AudioRecorder:
    def __init__(
        self,
        *,
        sample_rate: int = SAMPLE_RATE,
        stream_factory: Callable[..., Any] = _make_input_stream,
        refresh_devices: Callable[[], None] = _refresh_sounddevice,
    ) -> None:
        self.sample_rate = sample_rate
        self._stream_factory = stream_factory
        self._refresh_devices = refresh_devices
        self._lock = threading.Lock()
        self._stream: Any | None = None
        self._frames: list[np.ndarray] | None = None

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._stream is not None

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                raise RuntimeError("AudioRecorder is already recording")

        self._refresh_devices()
        frames: list[np.ndarray] = []

        def capture(indata: np.ndarray, *_args: Any) -> None:
            frames.append(indata.copy())

        stream = self._stream_factory(
            samplerate=self.sample_rate,
            channels=1,
            dtype="float32",
            callback=capture,
        )
        try:
            stream.start()
        except Exception:
            try:
                stream.close()
            finally:
                raise
        with self._lock:
            self._stream = stream
            self._frames = frames

    def stop(self) -> np.ndarray:
        stream, frames = self._detach()
        if stream is None:
            return np.empty(0, dtype=np.float32)
        try:
            stream.stop()
        finally:
            stream.close()
        if not frames:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(frames, axis=0).reshape(-1).astype(np.float32, copy=False)

    def abort(self) -> None:
        stream, _ = self._detach()
        if stream is None:
            return
        try:
            stream.stop()
        finally:
            stream.close()

    def _detach(self) -> tuple[Any | None, list[np.ndarray] | None]:
        with self._lock:
            stream = self._stream
            frames = self._frames
            self._stream = None
            self._frames = None
        return stream, frames
