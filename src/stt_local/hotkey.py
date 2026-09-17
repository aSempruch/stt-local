from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .coordinator import DictationState


DOUBLE_TAP_SECONDS = 0.20
LONG_PRESS_SECONDS = 0.70


def _timer_factory(delay: float, callback: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    return timer


class RightCommandGestures:
    """Turn Right Command presses into state-aware dictation actions."""

    def __init__(
        self,
        *,
        state: Callable[[], DictationState],
        toggle: Callable[[], None],
        submit: Callable[[], None],
        cancel: Callable[[], None],
        timer_factory: Callable[[float, Callable[[], None]], Any] = _timer_factory,
        double_tap_seconds: float = DOUBLE_TAP_SECONDS,
        long_press_seconds: float = LONG_PRESS_SECONDS,
    ) -> None:
        self._state = state
        self._toggle = toggle
        self._submit = submit
        self._cancel = cancel
        self._timer_factory = timer_factory
        self._double_tap_seconds = double_tap_seconds
        self._long_press_seconds = long_press_seconds
        self._lock = threading.RLock()
        self._is_down = False
        self._long_press_fired = False
        self._press_state = DictationState.IDLE
        self._second_tap = False
        self._ignore_short_tap = False
        self._hold_timer: Any | None = None
        self._single_tap_timer: Any | None = None
        self._start_guard_timer: Any | None = None
        self._hold_token: object | None = None
        self._single_tap_token: object | None = None
        self._start_guard_token: object | None = None

    def press(self) -> None:
        with self._lock:
            if self._is_down:
                return
            self._is_down = True
            self._long_press_fired = False
            self._press_state = self._state()
            self._ignore_short_tap = self._start_guard_timer is not None
            self._second_tap = self._single_tap_timer is not None
            if self._second_tap:
                self._single_tap_timer.cancel()
                self._single_tap_timer = None
                self._single_tap_token = None
            hold_token = object()
            self._hold_token = hold_token
            self._hold_timer = self._new_timer(
                self._long_press_seconds,
                lambda: self._fire_long_press(hold_token),
            )

    def release(self) -> None:
        action: Callable[[], None] | None = None
        with self._lock:
            if not self._is_down:
                return
            self._is_down = False
            if self._hold_timer is not None:
                self._hold_timer.cancel()
                self._hold_timer = None
                self._hold_token = None
            if self._long_press_fired:
                self._long_press_fired = False
                return
            if self._ignore_short_tap:
                self._ignore_short_tap = False
                return

            if self._press_state is DictationState.IDLE:
                action = self._toggle
                start_guard_token = object()
                self._start_guard_token = start_guard_token
                self._start_guard_timer = self._new_timer(
                    self._double_tap_seconds,
                    lambda: self._clear_start_guard(start_guard_token),
                )
            elif self._press_state in {
                DictationState.RECORDING_LOADING,
                DictationState.RECORDING_READY,
            }:
                if self._second_tap:
                    action = self._submit
                else:
                    single_tap_token = object()
                    self._single_tap_token = single_tap_token
                    self._single_tap_timer = self._new_timer(
                        self._double_tap_seconds,
                        lambda: self._fire_single_tap(single_tap_token),
                    )
            self._second_tap = False
        if action is not None:
            action()

    def stop(self) -> None:
        with self._lock:
            if self._hold_timer is not None:
                self._hold_timer.cancel()
                self._hold_timer = None
                self._hold_token = None
            if self._single_tap_timer is not None:
                self._single_tap_timer.cancel()
                self._single_tap_timer = None
                self._single_tap_token = None
            if self._start_guard_timer is not None:
                self._start_guard_timer.cancel()
                self._start_guard_timer = None
                self._start_guard_token = None
            self._is_down = False
            self._second_tap = False
            self._ignore_short_tap = False

    def _new_timer(self, delay: float, callback: Callable[[], None]) -> Any:
        timer = self._timer_factory(delay, callback)
        timer.start()
        return timer

    def _fire_long_press(self, token: object) -> None:
        with self._lock:
            if not self._is_down or self._hold_token is not token:
                return
            self._long_press_fired = True
            self._hold_timer = None
            self._hold_token = None
            if self._single_tap_timer is not None:
                self._single_tap_timer.cancel()
                self._single_tap_timer = None
                self._single_tap_token = None
        self._cancel()

    def _fire_single_tap(self, token: object) -> None:
        with self._lock:
            if self._single_tap_token is not token:
                return
            self._single_tap_timer = None
            self._single_tap_token = None
        self._toggle()

    def _clear_start_guard(self, token: object) -> None:
        with self._lock:
            if self._start_guard_token is not token:
                return
            self._start_guard_timer = None
            self._start_guard_token = None


def _listener_factory(
    on_press: Callable[[], None], on_release: Callable[[], None]
) -> Any:
    from pynput import keyboard

    return keyboard.Listener(
        on_press=lambda key: on_press() if key == keyboard.Key.cmd_r else None,
        on_release=lambda key: on_release() if key == keyboard.Key.cmd_r else None,
    )


class RightCommandMonitor:
    def __init__(
        self,
        gestures: RightCommandGestures,
        *,
        listener_factory: Callable[[Callable[[], None], Callable[[], None]], Any]
        = _listener_factory,
    ) -> None:
        self._gestures = gestures
        self._listener_factory = listener_factory
        self._listener: Any | None = None

    def start(self) -> None:
        listener = self._listener_factory(
            self._gestures.press, self._gestures.release
        )
        listener.start()
        listener.wait()
        if not listener.IS_TRUSTED:
            listener.stop()
            raise PermissionError(
                "Grant Accessibility permission to the STT Local Python executable"
            )
        self._listener = listener

    def stop(self) -> None:
        self._gestures.stop()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
