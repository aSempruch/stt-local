from stt_local.coordinator import DictationState
from stt_local.hotkey import RightCommandGestures, RightCommandMonitor


class FakeTimer:
    def __init__(self, delay, callback):
        self.delay = delay
        self.callback = callback
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True

    def fire(self):
        if not self.cancelled:
            self.callback()


def make_gestures(initial_state=DictationState.IDLE):
    current = [initial_state]
    actions = []
    timers = []

    def timer_factory(delay, callback):
        timer = FakeTimer(delay, callback)
        timers.append(timer)
        return timer

    gestures = RightCommandGestures(
        state=lambda: current[0],
        toggle=lambda: actions.append("toggle"),
        submit=lambda: actions.append("submit"),
        cancel=lambda: actions.append("cancel"),
        timer_factory=timer_factory,
    )
    return gestures, current, actions, timers


def test_idle_tap_starts_immediately_on_release_without_double_tap_delay():
    gestures, _, actions, timers = make_gestures()

    gestures.press()
    gestures.release()

    assert actions == ["toggle"]
    assert [timer.delay for timer in timers] == [0.70, 0.20]
    assert timers[0].cancelled


def test_second_fast_tap_after_start_is_ignored():
    gestures, current, actions, timers = make_gestures()
    gestures.press()
    gestures.release()
    current[0] = DictationState.RECORDING_LOADING

    gestures.press()
    gestures.release()
    timers[-2].fire()

    assert actions == ["toggle"]


def test_recording_single_tap_waits_for_short_double_tap_window():
    gestures, _, actions, timers = make_gestures(DictationState.RECORDING_READY)

    gestures.press()
    gestures.release()

    assert actions == []
    assert timers[-1].delay == 0.20
    timers[-1].fire()
    assert actions == ["toggle"]


def test_recording_double_tap_submits_and_cancels_pending_single_tap():
    gestures, _, actions, timers = make_gestures(DictationState.RECORDING_READY)
    gestures.press()
    gestures.release()
    single_tap_timer = timers[-1]

    gestures.press()
    gestures.release()

    assert single_tap_timer.cancelled
    assert actions == ["submit"]
    single_tap_timer.fire()
    assert actions == ["submit"]


def test_long_press_cancels_while_key_is_still_held_and_release_is_ignored():
    gestures, _, actions, timers = make_gestures(DictationState.RECORDING_READY)
    gestures.press()

    timers[-1].fire()

    assert actions == ["cancel"]
    gestures.release()
    assert actions == ["cancel"]


def test_idle_long_press_does_not_start_recording():
    gestures, _, actions, timers = make_gestures()
    gestures.press()
    timers[-1].fire()
    gestures.release()

    assert actions == ["cancel"]


def test_stop_cancels_pending_gesture_timers():
    gestures, _, actions, timers = make_gestures(DictationState.RECORDING_READY)
    gestures.press()
    gestures.release()

    gestures.stop()
    timers[-1].fire()

    assert actions == []


class FakeListener:
    def __init__(self, on_press, on_release, *, trusted=True):
        self.on_press = on_press
        self.on_release = on_release
        self.IS_TRUSTED = trusted
        self.started = False
        self.waited = False
        self.stopped = False

    def start(self):
        self.started = True

    def wait(self):
        self.waited = True

    def stop(self):
        self.stopped = True


def test_monitor_starts_and_stops_listener():
    gestures, *_ = make_gestures()
    listeners = []

    def listener_factory(on_press, on_release):
        listener = FakeListener(on_press, on_release)
        listeners.append(listener)
        return listener

    monitor = RightCommandMonitor(gestures, listener_factory=listener_factory)
    monitor.start()
    monitor.stop()

    assert listeners[0].started
    assert listeners[0].waited
    assert listeners[0].stopped


def test_monitor_rejects_untrusted_listener():
    gestures, *_ = make_gestures()
    listener = FakeListener(None, None, trusted=False)
    monitor = RightCommandMonitor(
        gestures, listener_factory=lambda _press, _release: listener
    )

    try:
        monitor.start()
    except PermissionError as exc:
        assert "Accessibility" in str(exc)
    else:
        raise AssertionError("expected missing permission to fail")
    assert listener.stopped
