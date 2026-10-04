from __future__ import annotations

import multiprocessing
import re
import subprocess
import sys
import threading
import traceback
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any

import numpy as np

from .constants import (
    DEFAULT_IDLE_UNLOAD_SECONDS,
    SPEECH_MODEL,
    SPEECH_SAMPLE_RATE,
    SPEECH_VOICE,
)
from .model_worker import describe_load_error, download_model, use_system_certificates


class SpeechError(RuntimeError):
    pass


_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str) -> list[str]:
    sentences: list[str] = []
    for line in text.splitlines():
        sentences.extend(part.strip() for part in _SENTENCE_END.split(line))
    return [sentence for sentence in sentences if sentence]


# Kokoro pads each sentence with its own silence (about 0.3 s before, 0.45 s
# after), so sentences were 0.75 s apart before any playback overhead. It is
# trimmed and replaced with a fixed pause picked by ear.
SILENCE_LEVEL = 0.01  # full-scale amplitude below which output counts as silence
EDGE_SECONDS = 0.02  # kept beyond the audible edges so soft consonants survive
SENTENCE_PAUSE_SECONDS = 0.4
# A player normally exits once its audio drains; past this much longer than the
# audio it was given, it is presumed stuck in CoreAudio and killed.
PLAYER_DRAIN_GRACE_SECONDS = 10.0


def shape_pauses(samples: np.ndarray, sample_rate: int) -> np.ndarray:
    """Trim a sentence's edge silence and end it with the sentence pause."""
    samples = np.asarray(samples, dtype=np.float32)
    loud = np.flatnonzero(np.abs(samples) > SILENCE_LEVEL)
    if not loud.size:
        return samples[:0]
    edge = int(sample_rate * EDGE_SECONDS)
    start, end = max(0, loud[0] - edge), min(len(samples), loud[-1] + 1 + edge)
    # The pause spans this sentence's trailing edge and the next one's leading edge.
    pad = max(0, int(sample_rate * SENTENCE_PAUSE_SECONDS) - 2 * edge)
    return np.concatenate([samples[start:end], np.zeros(pad, np.float32)])


def to_pcm(samples: np.ndarray) -> bytes:
    return (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def start_player(sample_rate: int) -> Any:
    """One player per reply, fed 16-bit PCM on stdin (see player.py)."""
    return subprocess.Popen(
        [sys.executable, "-m", "stt_local.player", str(sample_rate)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
    )


def _speech_worker_main(
    connection: Connection,
    ready_event: Any,
    model_name: str,
    voice: str,
) -> None:
    try:
        use_system_certificates()
        download_model(model_name, lambda _fraction: None)
        import mlx.core as mx
        from mlx_audio.tts.utils import load

        model = load(model_name)
        # Building the English pipeline loads spaCy and the pronunciation
        # lexicon; doing it now keeps the first reply as fast as later ones.
        for _result in model.generate("Ready.", voice=voice):
            pass
        ready_event.set()
        connection.send({"type": "ready"})
    except Exception as exc:
        traceback.print_exc()
        connection.send(
            {
                "type": "error",
                "fatal": True,
                "error": describe_load_error(exc, model_name, "voice model"),
            }
        )
        connection.close()
        return

    pending: dict[str, Any] | None = None
    while True:
        if pending is None:
            try:
                message = connection.recv()
            except EOFError:
                break
        else:
            message, pending = pending, None
        command = message.get("command")
        if command == "shutdown":
            break
        if command == "stop":
            continue
        if command != "speak":
            connection.send(
                {"type": "error", "error": f"Unknown speech command: {command}"}
            )
            connection.send({"type": "done"})
            continue
        try:
            # One sentence per line: Kokoro renders each line separately, so
            # playback starts after the first sentence instead of the whole text.
            text = "\n".join(split_sentences(message["text"]))
            for result in model.generate(
                text, voice=message.get("voice") or voice, split_pattern=r"\n+"
            ):
                samples = np.asarray(result.audio.astype(mx.float32))
                connection.send({"type": "audio", "samples": samples})
                # Any new command supersedes the rest of this reply.
                if connection.poll():
                    pending = connection.recv()
                    break
        except Exception as exc:
            traceback.print_exc()
            connection.send({"type": "error", "error": f"{type(exc).__name__}: {exc}"})
        connection.send({"type": "done"})
    connection.close()


class SpeechManager:
    """Speaks text with Kokoro in a worker process that loads on first use and
    exits after the idle delay, like the Whisper worker."""

    def __init__(
        self,
        *,
        model_name: str = SPEECH_MODEL,
        voice: Callable[[], str] = lambda: SPEECH_VOICE,
        sample_rate: int = SPEECH_SAMPLE_RATE,
        idle_seconds: float | Callable[[], float] = DEFAULT_IDLE_UNLOAD_SECONDS,
        on_error: Callable[[str], None] = lambda _message: None,
        context: Any | None = None,
        timer_factory: Callable[[float, Callable[[], None]], Any] = threading.Timer,
        thread_factory: Callable[..., Any] = threading.Thread,
        player_factory: Callable[[int], Any] = start_player,
    ) -> None:
        self.model_name = model_name
        self.voice = voice
        self.sample_rate = sample_rate
        self._idle_seconds = (
            idle_seconds if callable(idle_seconds) else lambda: idle_seconds
        )
        self._on_error = on_error
        self._context = context or multiprocessing.get_context("spawn")
        self._timer_factory = timer_factory
        self._thread_factory = thread_factory
        self._player_factory = player_factory
        self._state_lock = threading.RLock()
        self._request_lock = threading.Lock()
        self._process: Any | None = None
        self._connection: Any | None = None
        self._ready_event: Any | None = None
        self._idle_timer: Any | None = None
        self._player: Any | None = None
        self._generation = 0

    @property
    def idle_seconds(self) -> float:
        return float(self._idle_seconds())

    @property
    def is_running(self) -> bool:
        with self._state_lock:
            return self._process is not None and self._process.is_alive()

    def speak(self, text: str) -> None:
        """Replace anything being spoken with this text; returns immediately."""
        text = text.strip()
        if not text:
            return
        generation = self._interrupt()
        self._thread_factory(
            target=lambda: self._speak(generation, text),
            daemon=True,
            name="stt-local-speech",
        ).start()

    def stop(self) -> None:
        self._interrupt()

    def _interrupt(self) -> int:
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            player = self._player
            self._player = None
        if player is not None and player.poll() is None:
            player.terminate()
        return generation

    def _is_current(self, generation: int) -> bool:
        with self._state_lock:
            return generation == self._generation

    def _speak(self, generation: int, text: str) -> None:
        with self._request_lock:
            if not self._is_current(generation):
                return
            self.cancel_idle_shutdown()
            try:
                self._render(generation, text)
            except SpeechError as exc:
                self._on_error(str(exc))
            except (EOFError, BrokenPipeError, OSError) as exc:
                self._stop_worker(force=True)
                self._on_error(
                    f"The voice worker stopped unexpectedly "
                    f"({type(exc).__name__}: {exc})"
                )
            except Exception as exc:
                traceback.print_exc()
                self._stop_worker(force=True)
                self._on_error(f"{type(exc).__name__}: {exc}")
            finally:
                self.schedule_idle_shutdown()

    def _render(self, generation: int, text: str) -> None:
        self.ensure_started()
        with self._state_lock:
            connection = self._connection
        if connection is None:
            raise SpeechError("The voice worker connection is unavailable")
        # Started before synthesis so its startup overlaps the first sentence.
        player = self._start_player(generation)
        if player is None:
            return
        connection.send({"command": "speak", "text": text, "voice": self.voice()})
        stopped = False
        error: str | None = None
        seconds = 0.0
        try:
            while True:
                message = connection.recv()
                kind = message.get("type")
                if kind == "ready":
                    continue
                if kind == "error":
                    if message.get("fatal"):
                        self._stop_worker()
                        raise SpeechError(str(message.get("error")))
                    error = str(message.get("error"))
                    continue
                if kind == "done":
                    break
                if kind != "audio" or stopped:
                    continue
                pcm = to_pcm(shape_pauses(message["samples"], self.sample_rate))
                if self._feed(generation, player, pcm):
                    seconds += len(pcm) / 2 / self.sample_rate
                else:
                    connection.send({"command": "stop"})
                    stopped = True
        finally:
            returncode = self._finish_player(player, seconds)
        if error is not None:
            raise SpeechError(error)
        if returncode and self._is_current(generation):
            raise SpeechError(f"Speech playback failed (player exit status {returncode})")

    def _start_player(self, generation: int) -> Any | None:
        # Checked under the lock that stop() takes, so a stop can never be
        # followed by a player that was about to start.
        with self._state_lock:
            if generation != self._generation:
                return None
            self._player = self._player_factory(self.sample_rate)
            return self._player

    def _feed(self, generation: int, player: Any, pcm: bytes) -> bool:
        """Queue audio for the player; False once the reply was stopped or the
        player is gone. Blocks while the player is a pipe-full ahead."""
        if not self._is_current(generation):
            return False
        try:
            player.stdin.write(pcm)
            player.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            return False
        return True

    def _finish_player(self, player: Any, seconds: float) -> int | None:
        """Let the player drain (or wait out its termination); returns its exit status."""
        try:
            player.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            player.wait(timeout=seconds + PLAYER_DRAIN_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            player.kill()
            player.wait()
        with self._state_lock:
            if self._player is player:
                self._player = None
        return player.returncode

    def ensure_started(self) -> None:
        self.cancel_idle_shutdown()
        with self._state_lock:
            if self._process is not None and self._process.is_alive():
                return
            if self._connection is not None:
                self._connection.close()
            parent_connection, child_connection = self._context.Pipe()
            ready_event = self._context.Event()
            process = self._context.Process(
                target=_speech_worker_main,
                args=(child_connection, ready_event, self.model_name, self.voice()),
                daemon=True,
                name="stt-local-kokoro",
            )
            process.start()
            close_child = getattr(child_connection, "close", None)
            if close_child is not None:
                close_child()
            self._connection = parent_connection
            self._ready_event = ready_event
            self._process = process

    def schedule_idle_shutdown(self) -> None:
        self.cancel_idle_shutdown()
        timer = self._timer_factory(self.idle_seconds, self.shutdown)
        timer.daemon = True
        with self._state_lock:
            self._idle_timer = timer
        timer.start()

    def reschedule_idle_shutdown(self) -> None:
        with self._state_lock:
            pending = self._idle_timer is not None
        if pending:
            self.schedule_idle_shutdown()

    def cancel_idle_shutdown(self) -> None:
        with self._state_lock:
            timer = self._idle_timer
            self._idle_timer = None
        if timer is not None:
            timer.cancel()

    def shutdown(self) -> None:
        """Unload the model; also used when quitting."""
        self.cancel_idle_shutdown()
        self._interrupt()
        with self._request_lock:
            self._stop_worker()

    def close(self) -> None:
        self.shutdown()

    def _stop_worker(self, *, force: bool = False) -> None:
        with self._state_lock:
            process = self._process
            connection = self._connection
            self._process = None
            self._connection = None
            self._ready_event = None
        if connection is not None and not force:
            try:
                if process is not None and process.is_alive():
                    connection.send({"command": "shutdown"})
            except (BrokenPipeError, EOFError, OSError):
                pass
        if process is not None:
            if force and process.is_alive():
                process.terminate()
            process.join(timeout=2.0)
            if process.is_alive():
                process.terminate()
                process.join(timeout=2.0)
        if connection is not None:
            connection.close()
