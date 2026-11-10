from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from stt_local.output import (
    AUTO_GENERATED_TYPE,
    RESTORE_DELAY_SECONDS,
    STRING_TYPE,
    TRANSIENT_TYPE,
    MacOutput,
)
from stt_local.sounds import MacSounds


class FakeKeyboard:
    def __init__(self):
        self.pressed_key = None
        self.tapped = []

    @contextmanager
    def pressed(self, key):
        self.pressed_key = key
        yield

    def tap(self, key):
        self.tapped.append(key)


class FakePasteboard:
    def __init__(self, items=None):
        self.items = items if items is not None else []
        self.change_count = 0
        self.writes = []

    def snapshot(self):
        return [dict(item) for item in self.items]

    def write(self, items):
        self.items = [dict(item) for item in items]
        self.writes.append(self.items)
        self.change_count += 1

    def text(self):
        return self.items[0][STRING_TYPE].decode() if self.items else None


class FakeTimers:
    def __init__(self):
        self.pending = []

    def __call__(self, delay, callback):
        timer = Mock()
        timer.delay = delay
        timer.callback = callback
        timer.cancel.side_effect = lambda: self.pending.remove(timer)
        self.pending.append(timer)
        return timer

    def fire(self):
        timer = self.pending.pop(0)
        timer.callback()


def make_output(pasteboard=None, keyboard=None, **kwargs):
    pasteboard = pasteboard if pasteboard is not None else FakePasteboard()
    keyboard = keyboard if keyboard is not None else FakeKeyboard()
    timers = FakeTimers()
    output = MacOutput(
        pasteboard_factory=lambda: pasteboard,
        keyboard_factory=lambda: keyboard,
        command_key="COMMAND",
        enter_key="ENTER",
        sleep=Mock(),
        timer_factory=timers,
        **kwargs,
    )
    return output, pasteboard, keyboard, timers


def test_output_copies_utf8_as_transient_then_pastes():
    output, pasteboard, keyboard, _ = make_output()

    output.send("héllo")

    item = pasteboard.items[0]
    assert item[STRING_TYPE] == "héllo".encode()
    # Alfred and other clipboard managers skip items carrying these markers.
    assert TRANSIENT_TYPE in item and AUTO_GENERATED_TYPE in item
    assert keyboard.pressed_key == "COMMAND"
    assert keyboard.tapped == ["v"]


def test_submit_output_pastes_then_presses_enter():
    output, _, keyboard, _ = make_output()

    output.send("send it", press_enter=True)

    assert keyboard.tapped == ["v", "ENTER"]


def test_previous_clipboard_comes_back_after_the_paste():
    previous = [{"public.png": b"PNG"}, {STRING_TYPE: b"copied"}]
    output, pasteboard, _, timers = make_output(FakePasteboard(previous))

    output.send("dictated")
    assert pasteboard.text() == "dictated"
    assert timers.pending[0].delay == RESTORE_DELAY_SECONDS

    timers.fire()

    assert pasteboard.items[0]["public.png"] == b"PNG"
    assert TRANSIENT_TYPE in pasteboard.items[0]
    assert pasteboard.items[1] == {STRING_TYPE: b"copied"}


def test_empty_clipboard_is_restored_empty():
    output, pasteboard, _, timers = make_output()

    output.send("dictated")
    timers.fire()

    assert pasteboard.items == []


def test_a_copy_after_the_paste_is_not_overwritten():
    output, pasteboard, _, timers = make_output(
        FakePasteboard([{STRING_TYPE: b"old"}])
    )

    output.send("dictated")
    pasteboard.write([{STRING_TYPE: b"new copy"}])
    timers.fire()

    assert pasteboard.text() == "new copy"


def test_back_to_back_pastes_restore_the_original_contents():
    output, pasteboard, _, timers = make_output(
        FakePasteboard([{STRING_TYPE: b"original"}])
    )

    output.send("first")
    output.send("second")
    assert len(timers.pending) == 1

    timers.fire()

    assert pasteboard.text() == "original"


def test_stale_restore_callback_does_nothing():
    output, pasteboard, _, timers = make_output(
        FakePasteboard([{STRING_TYPE: b"original"}])
    )
    output.send("first")
    stale = timers.pending[0].callback
    output.send("second")

    stale()

    assert pasteboard.text() == "second"


def test_failed_keystroke_still_restores_the_clipboard():
    keyboard = FakeKeyboard()
    keyboard.tap = Mock(side_effect=RuntimeError("no access"))
    output, pasteboard, _, timers = make_output(
        FakePasteboard([{STRING_TYPE: b"original"}]), keyboard
    )

    with pytest.raises(RuntimeError):
        output.send("dictated")
    timers.fire()

    assert pasteboard.text() == "original"


def test_failed_clipboard_write_puts_the_old_contents_back():
    pasteboard = FakePasteboard([{STRING_TYPE: b"original"}])
    real_write = pasteboard.write
    calls = []

    def write(items):
        calls.append(items)
        if len(calls) == 1:
            raise RuntimeError("busy")
        real_write(items)

    pasteboard.write = write
    output, _, keyboard, timers = make_output(pasteboard)

    with pytest.raises(RuntimeError, match="busy"):
        output.send("dictated")

    assert pasteboard.text() == "original"
    assert keyboard.tapped == []
    assert timers.pending == []


def test_sounds_are_preloaded_and_played_in_process():
    loaded = {}

    def load(path):
        loaded[path.name] = Mock()
        return loaded[path.name]

    sounds = MacSounds(load=load)
    assert set(loaded) == {
        "start-recording.wav",
        "Ping.aiff",
        "Pop.aiff",
        "submit.wav",
    }
    assert not any(sound.play.called for sound in loaded.values())

    sounds.play_start()
    sounds.play_stop()
    sounds.play_cancel()
    sounds.play_submit()

    for sound in loaded.values():
        sound.stop.assert_called_once_with()
        sound.play.assert_called_once_with()


def test_sounds_skip_cues_that_failed_to_load():
    sounds = MacSounds(load=lambda _path: None)

    sounds.play_start()


def test_default_cues_load_from_disk():
    sounds = MacSounds()

    assert sounds._start_sound is not None
    assert sounds._stop_sound is not None
    assert sounds._cancel_sound is not None
    assert sounds._submit_sound is not None


def test_pasteboard_and_keystrokes_run_through_main_thread_hook():
    events = []
    keyboard = FakeKeyboard()
    pasteboard = FakePasteboard()
    pasteboard.write = lambda items: events.append("copy")

    def on_main(callback):
        events.append("main")
        callback()

    output = MacOutput(
        pasteboard_factory=lambda: pasteboard,
        keyboard_factory=lambda: events.append("keyboard") or keyboard,
        command_key="COMMAND",
        sleep=Mock(),
        on_main=on_main,
        timer_factory=FakeTimers(),
    )

    output.send("hi")

    assert events == ["main", "copy", "main", "keyboard", "main"]
    assert keyboard.tapped == ["v"]


def test_call_on_main_thread_runs_inline_when_already_main():
    from stt_local.output import call_on_main_thread

    schedule = Mock()
    calls = []

    call_on_main_thread(
        lambda: calls.append(1), is_main=lambda: True, schedule=schedule
    )

    assert calls == [1]
    schedule.assert_not_called()


def test_call_on_main_thread_waits_and_propagates_errors():
    import pytest

    from stt_local.output import call_on_main_thread

    scheduled = []

    def schedule(callback):
        scheduled.append(callback)
        callback()

    calls = []
    call_on_main_thread(
        lambda: calls.append(1), is_main=lambda: False, schedule=schedule
    )
    assert calls == [1] and len(scheduled) == 1

    def fail():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        call_on_main_thread(fail, is_main=lambda: False, schedule=schedule)


def test_call_on_main_thread_times_out_when_main_is_blocked():
    import pytest

    from stt_local.output import call_on_main_thread

    with pytest.raises(TimeoutError):
        call_on_main_thread(
            lambda: None,
            is_main=lambda: False,
            schedule=lambda _callback: None,
            timeout=0.01,
        )
