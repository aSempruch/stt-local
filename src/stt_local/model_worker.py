from __future__ import annotations

import multiprocessing
import threading
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

import numpy as np

from .audio import trim_trailing_silence
from .constants import (
    DEFAULT_IDLE_UNLOAD_SECONDS,
    LANGUAGE,
    MODEL,
    SAMPLE_RATE,
)


class TranscriptionError(RuntimeError):
    pass


class TranscriptionCancelled(TranscriptionError):
    pass


def _worker_main(
    connection: Connection,
    ready_event: Any,
    model_name: str,
    language: str,
    sample_rate: int,
) -> None:
    try:
        import mlx_whisper

        options = {
            "path_or_hf_repo": model_name,
            "language": language,
            "condition_on_previous_text": False,
            "verbose": None,
        }
        mlx_whisper.transcribe(
            np.zeros(sample_rate // 10, dtype=np.float32), **options
        )
        ready_event.set()
        connection.send({"type": "ready"})
    except Exception as exc:
        connection.send({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        connection.close()
        return

    while True:
        try:
            message = connection.recv()
        except EOFError:
            break
        command = message.get("command")
        if command == "shutdown":
            break
        if command != "transcribe":
            connection.send(
                {"type": "error", "error": f"Unknown worker command: {command}"}
            )
            continue
        try:
            audio = np.asarray(message["audio"], dtype=np.float32).reshape(-1)
            audio = trim_trailing_silence(audio, sample_rate)
            result = mlx_whisper.transcribe(audio, **options)
            connection.send({"type": "result", "text": result["text"].strip()})
        except Exception as exc:
            connection.send(
                {"type": "error", "error": f"{type(exc).__name__}: {exc}"}
            )
    connection.close()


class WorkerManager:
    def __init__(
        self,
        *,
        model_name: str = MODEL,
        language: str = LANGUAGE,
        sample_rate: int = SAMPLE_RATE,
        idle_seconds: float = DEFAULT_IDLE_UNLOAD_SECONDS,
        context: Any | None = None,
        timer_factory: Callable[[float, Callable[[], None]], Any] = threading.Timer,
    ) -> None:
        self.model_name = model_name
        self.language = language
        self.sample_rate = sample_rate
        self.idle_seconds = idle_seconds
        self._context = context or multiprocessing.get_context("spawn")
        self._timer_factory = timer_factory
        self._state_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._process: Any | None = None
        self._connection: Any | None = None
        self._ready_event: Any | None = None
        self._idle_timer: Any | None = None
        self._generation = 0

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._process is not None and self._process.is_alive()

    @property
    def is_ready(self) -> bool:
        with self._state_lock:
            return bool(
                self._process is not None
                and self._process.is_alive()
                and self._ready_event is not None
                and self._ready_event.is_set()
            )

    def ensure_started(self) -> None:
        self.cancel_idle_shutdown()
        with self._state_lock:
            if self._process is not None and self._process.is_alive():
                return
            self._close_stale_handles_locked()
            parent_connection, child_connection = self._context.Pipe()
            ready_event = self._context.Event()
            process = self._context.Process(
                target=_worker_main,
                args=(
                    child_connection,
                    ready_event,
                    self.model_name,
                    self.language,
                    self.sample_rate,
                ),
                daemon=True,
                name="stt-local-whisper",
            )
            process.start()
            close_child = getattr(child_connection, "close", None)
            if close_child is not None:
                close_child()
            self._connection = parent_connection
            self._ready_event = ready_event
            self._process = process

    def transcribe(self, audio: np.ndarray) -> str:
        with self._request_lock:
            with self._state_lock:
                generation = self._generation
            last_error: Exception | None = None
            for _attempt in range(2):
                try:
                    text = self._transcribe_once(audio)
                    self.schedule_idle_shutdown()
                    return text
                except (EOFError, BrokenPipeError, OSError, TranscriptionError) as exc:
                    with self._state_lock:
                        if generation != self._generation:
                            raise TranscriptionCancelled from exc
                    last_error = exc
                    self._stop_worker()
            raise TranscriptionError(
                f"Transcription failed after retry: {last_error}"
            ) from last_error

    def _transcribe_once(self, audio: np.ndarray) -> str:
        self.ensure_started()
        with self._state_lock:
            connection = self._connection
        if connection is None:
            raise TranscriptionError("Worker connection is unavailable")
        connection.send(
            {"command": "transcribe", "audio": np.asarray(audio, dtype=np.float32)}
        )
        while True:
            message = connection.recv()
            message_type = message.get("type")
            if message_type == "ready":
                continue
            if message_type == "result":
                return str(message.get("text", ""))
            if message_type == "error":
                raise TranscriptionError(str(message.get("error", "Worker error")))
            raise TranscriptionError(f"Unknown worker response: {message_type}")

    def schedule_idle_shutdown(self) -> None:
        self.cancel_idle_shutdown()
        timer = self._timer_factory(self.idle_seconds, self.shutdown)
        timer.daemon = True
        with self._state_lock:
            self._idle_timer = timer
        timer.start()

    def cancel_idle_shutdown(self) -> None:
        with self._state_lock:
            timer = self._idle_timer
            self._idle_timer = None
        if timer is not None:
            timer.cancel()

    def shutdown(self) -> None:
        self.cancel_idle_shutdown()
        with self._request_lock:
            self._stop_worker()

    def cancel(self) -> None:
        self.cancel_idle_shutdown()
        with self._state_lock:
            self._generation += 1
        self._stop_worker()

    def _stop_worker(self) -> None:
        with self._state_lock:
            process = self._process
            connection = self._connection
            self._process = None
            self._connection = None
            self._ready_event = None
        if connection is not None:
            try:
                if process is not None and process.is_alive():
                    connection.send({"command": "shutdown"})
            except (BrokenPipeError, EOFError, OSError):
                pass
        if process is not None:
            process.join(timeout=2.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
        if connection is not None:
            connection.close()

    def _close_stale_handles_locked(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._process = None
        self._connection = None
        self._ready_event = None
