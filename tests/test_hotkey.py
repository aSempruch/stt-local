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


def make_gestures(initial_state=DictationState.IDLE, clock=None):
    current = [initial_state]
    actions = []
    timers = []

    def timer_factory(delay, callback):
        timer = FakeTimer(delay, callback)
        timers.append(timer)
        return timer

    options = {"clock": clock} if clock is not None else {}
    gestures = RightCommandGestures(
        state=lambda: current[0],
        toggle=lambda: actions.append("toggle"),
        submit=lambda: actions.append("submit"),
        cancel=lambda: actions.append("cancel"),
        timer_factory=timer_factory,
        **options,
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
    now = [0.0]
    gestures, _, actions, timers = make_gestures(
        DictationState.RECORDING_READY, clock=lambda: now[0]
    )
    gestures.press()
    gestures.release()
    single_tap_timer = timers[-1]

    now[0] = 0.10
    gestures.press()
    gestures.release()

    assert single_tap_timer.cancelled
    assert actions == ["submit"]
    single_tap_timer.fire()
    assert actions == ["submit"]


def test_very_fast_duplicate_press_does_not_turn_single_tap_into_submit():
    now = [0.0]
    gestures, _, actions, timers = make_gestures(
        DictationState.RECORDING_READY, clock=lambda: now[0]
    )
    gestures.press()
    gestures.release()
    single_tap_timer = timers[-1]

    now[0] = 0.02
    gestures.press()
    gestures.release()
    single_tap_timer.fire()

    assert actions == ["toggle"]


def test_long_press_cancels_only_on_release():
    gestures, _, actions, timers = make_gestures(DictationState.RECORDING_READY)
    gestures.press()

    timers[-1].fire()

    assert actions == []
    gestures.release()
    assert actions == ["cancel"]


def test_idle_long_press_does_not_start_recording():
    gestures, _, actions, timers = make_gestures()
    gestures.press()
    timers[-1].fire()
    gestures.release()

    assert actions == ["cancel"]


def test_command_shortcut_does_not_start_recording():
    gestures, _, actions, timers = make_gestures()
    gestures.press()
    gestures.other_key_pressed()
    assert timers[-1].cancelled
    gestures.release()

    assert actions == []


def test_command_shortcut_does_not_stop_recording():
    gestures, _, actions, _ = make_gestures(DictationState.RECORDING_READY)
    gestures.press()
    gestures.other_key_pressed()
    gestures.release()

    assert actions == []


def test_command_shortcut_after_hold_threshold_does_not_cancel():
    gestures, _, actions, timers = make_gestures(DictationState.RECORDING_READY)
    gestures.press()
    timers[-1].fire()
    gestures.other_key_pressed()
    gestures.release()

    assert actions == []


def test_command_shortcut_does_not_prevent_next_solo_tap():
    gestures, _, actions, _ = make_gestures()
    gestures.press()
    gestures.other_key_pressed()
    gestures.release()
    gestures.press()
    gestures.release()

    assert actions == ["toggle"]


def test_stop_cancels_pending_gesture_timers():
    gestures, _, actions, timers = make_gestures(DictationState.RECORDING_READY)
    gestures.press()
    gestures.release()

    gestures.stop()
    timers[-1].fire()

    assert actions == []


class FakeListener:
    def __init__(self, on_press, on_release, on_other_key=None, *, trusted=True):
        self.on_press = on_press
        self.on_release = on_release
        self.on_other_key = on_other_key
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

    def listener_factory(on_press, on_release, on_other_key):
        listener = FakeListener(on_press, on_release, on_other_key)
        listeners.append(listener)
        return listener

    monitor = RightCommandMonitor(gestures, listener_factory=listener_factory)
    monitor.start()
    monitor.stop()

    assert listeners[0].started
    assert listeners[0].waited
    assert listeners[0].stopped


def test_monitor_forwards_other_key_to_gestures():
    gestures, _, actions, _ = make_gestures()
    listeners = []

    def listener_factory(on_press, on_release, on_other_key):
        listener = FakeListener(on_press, on_release, on_other_key)
        listeners.append(listener)
        return listener

    monitor = RightCommandMonitor(gestures, listener_factory=listener_factory)
    monitor.start()
    listeners[0].on_press()
    listeners[0].on_other_key()
    listeners[0].on_release()
    assert actions == []
    monitor.stop()


def test_monitor_rejects_untrusted_listener():
    gestures, *_ = make_gestures()
    listener = FakeListener(None, None, trusted=False)
    monitor = RightCommandMonitor(
        gestures, listener_factory=lambda _press, _release, _other: listener
    )

    try:
        monitor.start()
    except PermissionError as exc:
        assert "Accessibility" in str(exc)
    else:
        raise AssertionError("expected missing permission to fail")
    assert listener.stopped
