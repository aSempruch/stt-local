from contextlib import contextmanager
from unittest.mock import Mock

from local_dictation.output import MacOutput
from local_dictation.sounds import MacSounds


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


def test_sounds_launch_without_waiting():
    popen = Mock()
    sounds = MacSounds(popen=popen)

    sounds.play_start()
    sounds.play_stop()

    assert popen.call_count == 2
    assert popen.call_args_list[0].args[0][-1].endswith("Tink.aiff")
    assert popen.call_args_list[1].args[0][-1].endswith("Pop.aiff")
