from contextlib import contextmanager
from unittest.mock import Mock

from stt_local.output import MacOutput
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


def test_output_copies_utf8_then_pastes():
    run = Mock()
    keyboard = FakeKeyboard()
    sleep = Mock()
    output = MacOutput(
        run=run,
        keyboard_factory=lambda: keyboard,
        command_key="COMMAND",
        sleep=sleep,
    )

    output.send("héllo")

    run.assert_called_once_with(
        ["pbcopy"], input="héllo".encode(), check=True
    )
    sleep.assert_called_once_with(0.05)
    assert keyboard.pressed_key == "COMMAND"
    assert keyboard.tapped == ["v"]


def test_submit_output_pastes_then_presses_enter():
    keyboard = FakeKeyboard()
    output = MacOutput(
        run=Mock(),
        keyboard_factory=lambda: keyboard,
        command_key="COMMAND",
        enter_key="ENTER",
        sleep=Mock(),
    )

    output.send("send it", press_enter=True)

    assert keyboard.tapped == ["v", "ENTER"]


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


def test_keystrokes_run_through_main_thread_hook_after_copy():
    events = []
    keyboard = FakeKeyboard()

    def on_main(callback):
        events.append("main")
        callback()

    output = MacOutput(
        run=lambda *_args, **_kwargs: events.append("copy"),
        keyboard_factory=lambda: events.append("keyboard") or keyboard,
        command_key="COMMAND",
        sleep=Mock(),
        on_main=on_main,
    )

    output.send("hi")

    assert events == ["copy", "main", "keyboard"]
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
