from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable
from typing import Any

import numpy as np

from .constants import SAMPLE_RATE, STREAM_STOP_TIMEOUT_SECONDS

LEVEL_HISTORY = 128


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
        thread_factory: Callable[..., Any] = threading.Thread,
        start_timeout: float = 5.0,
        stop_timeout: float = STREAM_STOP_TIMEOUT_SECONDS,
    ) -> None:
        self.sample_rate = sample_rate
        self._stream_factory = stream_factory
        self._refresh_devices = refresh_devices
        self._thread_factory = thread_factory
        self._start_timeout = start_timeout
        self._stop_timeout = stop_timeout
        self._lock = threading.Lock()
        self._stream: Any | None = None
        self._frames: list[np.ndarray] | None = None
        self._levels: deque[float] | None = None
        self._needs_restart = False

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._stream is not None

    @property
    def needs_restart(self) -> bool:
        with self._lock:
            return self._needs_restart

    def recent_levels(self, count: int) -> list[float]:
        """RMS of the most recent capture blocks, oldest first."""
        with self._lock:
            levels = self._levels
        if levels is None or count <= 0:
            return []
        return list(levels)[-count:]

    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                raise RuntimeError("AudioRecorder is already recording")

        frames: list[np.ndarray] = []
        levels: deque[float] = deque(maxlen=LEVEL_HISTORY)

        def capture(indata: np.ndarray, *_args: Any) -> None:
            frames.append(indata.copy())
            if indata.size:
                levels.append(float(np.sqrt(np.mean(np.square(indata)))))

        result: dict[str, Any] = {}
        completed = threading.Event()
        handoff_lock = threading.Lock()
        abandoned = False

        def open_stream() -> None:
            nonlocal abandoned
            try:
                self._refresh_devices()
                stream = self._stream_factory(
                    samplerate=self.sample_rate,
                    channels=1,
                    dtype="float32",
                    callback=capture,
                )
                try:
                    stream.start()
                except Exception:
                    stream.close()
                    raise
                with handoff_lock:
                    close_abandoned = abandoned
                    if not close_abandoned:
                        result["stream"] = stream
                if close_abandoned:
                    try:
                        stream.stop()
                    finally:
                        stream.close()
                    return
            except Exception as exc:
                result["error"] = exc
            finally:
                completed.set()

        thread = self._thread_factory(target=open_stream, daemon=True)
        thread.start()
        if not completed.wait(self._start_timeout):
            self._mark_needs_restart()
            with handoff_lock:
                abandoned = True
                orphaned_stream = result.get("stream")
            if orphaned_stream is not None:
                self._thread_factory(
                    target=lambda: self._teardown(orphaned_stream), daemon=True
                ).start()
            raise TimeoutError("Opening the microphone timed out")
        if "error" in result:
            raise result["error"]
        stream = result["stream"]
        with self._lock:
            self._stream = stream
            self._frames = frames
            self._levels = levels

    def stop(self) -> np.ndarray:
        stream, frames = self._detach()
        if stream is None:
            return np.empty(0, dtype=np.float32)
        self._teardown(stream)
        if not frames:
            return np.empty(0, dtype=np.float32)
        return np.concatenate(frames, axis=0).reshape(-1).astype(np.float32, copy=False)

    def abort(self) -> None:
        stream, _ = self._detach()
        if stream is None:
            return
        self._teardown(stream)

    def _teardown(self, stream: Any) -> None:
        # stream.stop()/close() call straight into CoreAudio with no timeout of
        # its own; it can deadlock inside the OS audio HAL (observed in the
        # wild). Run it on a thread and abandon that thread on timeout so a
        # stuck native call can't freeze recording forever.
        def close() -> None:
            try:
                stream.stop()
            finally:
                stream.close()

        thread = self._thread_factory(target=close, daemon=True)
        thread.start()
        thread.join(self._stop_timeout)
        if thread.is_alive():
            self._mark_needs_restart()

    def _mark_needs_restart(self) -> None:
        with self._lock:
            self._needs_restart = True

    def _detach(self) -> tuple[Any | None, list[np.ndarray] | None]:
        with self._lock:
            stream = self._stream
            frames = self._frames
            self._stream = None
            self._frames = None
            self._levels = None
        return stream, frames
