from __future__ import annotations

import threading
from collections.abc import Callable
from enum import Enum
from typing import Any


class DictationState(Enum):
    IDLE = "idle"
    RECORDING_LOADING = "recording_loading"
    RECORDING_READY = "recording_ready"
    STOPPING = "stopping"
    TRANSCRIBING = "transcribing"
    ERROR = "error"


class DictationCoordinator:
    def __init__(
        self,
        *,
        recorder: Any,
        worker: Any,
        processors: Any,
        output: Any,
        sounds: Any,
        selected_processor: Callable[[], str],
        status_callback: Callable[[DictationState], None] = lambda _state: None,
        notification_callback: Callable[[str, str], None] = lambda _title, _message: None,
        thread_factory: Callable[..., Any] = threading.Thread,
        dispatch: Callable[[Callable[[], None]], None] = lambda callback: callback(),
        event_callback: Callable[[str], None] = lambda _name: None,
    ) -> None:
        self.recorder = recorder
        self.worker = worker
        self.processors = processors
        self.output = output
        self.sounds = sounds
        self.selected_processor = selected_processor
        self.status_callback = status_callback
        self.notification_callback = notification_callback
        self.thread_factory = thread_factory
        self.dispatch = dispatch
        self.event_callback = event_callback
        self._lock = threading.RLock()
        self._state = DictationState.IDLE
        self._cancel_requested = threading.Event()
        self._shutdown_requested = threading.Event()

    @property
    def state(self) -> DictationState:
        with self._lock:
            return self._state

    def toggle(self) -> None:
        state = self.state
        if state is DictationState.IDLE:
            self.start_recording()
        elif state in {
            DictationState.RECORDING_LOADING,
            DictationState.RECORDING_READY,
        }:
            self.stop_recording()

    def start_recording(self) -> None:
        with self._lock:
            if self._state is not DictationState.IDLE:
                return
        try:
            self._cancel_requested.clear()
            self.event_callback("cancel_idle")
            self.worker.cancel_idle_shutdown()
            self.event_callback("capture_start")
            self.recorder.start()
            self.event_callback("start_sound")
            self.sounds.play_start()
            self.event_callback("worker_start")
            self.worker.ensure_started()
            ready = bool(self.worker.is_ready)
            self._set_state(
                DictationState.RECORDING_READY
                if ready
                else DictationState.RECORDING_LOADING
            )
        except Exception as exc:
            try:
                self.recorder.abort()
            except Exception:
                pass
            self._notify("Recording failed", str(exc))
            self._set_state(DictationState.IDLE)

    def refresh(self) -> None:
        if self.state is DictationState.RECORDING_LOADING and self.worker.is_ready:
            self._set_state(DictationState.RECORDING_READY)

    def stop_recording(self, *, submit: bool = False) -> None:
        with self._lock:
            if self._state not in {
                DictationState.RECORDING_LOADING,
                DictationState.RECORDING_READY,
            }:
                return
            self._state = DictationState.STOPPING
        self._emit_status(DictationState.STOPPING)
        if submit:
            self.sounds.play_submit()
        else:
            self.sounds.play_stop()
        thread = self.thread_factory(
            target=lambda: self._finish_recording(submit), daemon=True
        )
        thread.start()

    def cancel(self) -> None:
        state = self.state
        if state in {
            DictationState.RECORDING_LOADING,
            DictationState.RECORDING_READY,
        }:
            self._cancel_requested.set()
            self._set_state(DictationState.STOPPING)
            self.sounds.play_cancel()
            thread = self.thread_factory(target=self._discard_recording, daemon=True)
            thread.start()
        elif state in {DictationState.STOPPING, DictationState.TRANSCRIBING}:
            self._cancel_requested.set()
            self.worker.cancel()

    def _discard_recording(self) -> None:
        try:
            self.recorder.abort()
        except Exception as exc:
            self._notify("Recording cancel failed", str(exc))
        finally:
            if not self._shutdown_requested.is_set():
                self.worker.schedule_idle_shutdown()
            self._set_state(DictationState.IDLE)

    def _finish_recording(self, submit: bool = False) -> None:
        try:
            audio = self.recorder.stop()
            if audio.size == 0 or self._cancel_requested.is_set():
                return
            self._set_state(DictationState.TRANSCRIBING)
            raw_text = self.worker.transcribe(audio).strip()
            if not raw_text or self._cancel_requested.is_set():
                return
            processed = self.processors.apply(self.selected_processor(), raw_text)
            if processed.error:
                self._notify("Processor failed", processed.error)
            final_text = processed.text.strip()
            if not final_text:
                return
            try:
                self.output.send(final_text, press_enter=submit)
            except Exception as exc:
                self._notify("Paste failed", str(exc))
        except Exception as exc:
            if not self._cancel_requested.is_set():
                self._notify("Transcription failed", str(exc))
        finally:
            if not self._shutdown_requested.is_set():
                self.worker.schedule_idle_shutdown()
            self._set_state(DictationState.IDLE)

    def shutdown(self) -> None:
        self._shutdown_requested.set()
        self._cancel_requested.set()
        if self.state in {
            DictationState.RECORDING_LOADING,
            DictationState.RECORDING_READY,
        }:
            try:
                self.recorder.abort()
            except Exception:
                pass
        # An active transcription holds the worker request lock. Interrupting it
        # avoids deadlocking the menu-bar thread before the app can actually quit.
        self.worker.cancel()
        self._set_state(DictationState.IDLE)

    def _set_state(self, state: DictationState) -> None:
        with self._lock:
            self._state = state
        self._emit_status(state)

    def _emit_status(self, state: DictationState) -> None:
        self.dispatch(lambda: self.status_callback(state))

    def _notify(self, title: str, message: str) -> None:
        self.dispatch(lambda: self.notification_callback(title, message))
