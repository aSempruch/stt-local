import shutil
import socket
import tempfile
from pathlib import Path

import pytest

from stt_local.control import (
    CommandHandler,
    ControlServer,
    ControlSocketInUse,
    send_command,
)


@pytest.fixture
def socket_path():
    # pytest's tmp_path is too long for an AF_UNIX path on macOS.
    directory = Path(tempfile.mkdtemp(prefix="stt-", dir="/tmp"))
    yield directory / "control.sock"
    shutil.rmtree(directory, ignore_errors=True)


class FakeSpeech:
    def __init__(self):
        self.spoken = []
        self.stops = 0

    def speak(self, text):
        self.spoken.append(text)

    def stop(self):
        self.stops += 1


def make_handler(enabled=False):
    speech = FakeSpeech()
    state = {"enabled": enabled}
    handler = CommandHandler(
        speech=speech,
        is_enabled=lambda: state["enabled"],
        set_enabled=lambda value: state.update(enabled=value),
    )
    return handler, speech, state


def test_round_trip_through_socket(socket_path):
    server = ControlServer(lambda request: {"ok": True, "echo": request}, socket_path)
    server.start()
    try:
        assert send_command({"command": "status"}, path=socket_path) == {
            "ok": True,
            "echo": {"command": "status"},
        }
        assert socket_path.stat().st_mode & 0o777 == 0o600
    finally:
        server.close()
    assert not socket_path.exists()


def test_client_returns_none_when_app_not_running(socket_path):
    assert send_command({"command": "status"}, path=socket_path) is None


def test_handler_errors_become_error_replies(socket_path):
    def broken(_request):
        raise ValueError("bad")

    server = ControlServer(broken, socket_path)
    server.start()
    try:
        reply = send_command({"command": "status"}, path=socket_path)
    finally:
        server.close()
    assert reply == {"ok": False, "error": "ValueError: bad"}


def test_stale_socket_file_is_replaced(socket_path):
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_path))
    stale.close()

    server = ControlServer(lambda _r: {"ok": True}, socket_path)
    server.start()
    try:
        assert send_command({}, path=socket_path) == {"ok": True}
    finally:
        server.close()


def test_live_socket_is_not_stolen(socket_path):
    first = ControlServer(lambda _r: {"ok": True}, socket_path)
    first.start()
    try:
        with pytest.raises(ControlSocketInUse):
            ControlServer(lambda _r: {"ok": True}, socket_path).start()
        assert send_command({}, path=socket_path) == {"ok": True}
    finally:
        first.close()


def test_conditional_speak_respects_toggle():
    handler, speech, state = make_handler(enabled=False)

    reply = handler({"command": "speak", "text": "Hi.", "if_enabled": True})
    assert reply == {"ok": True, "enabled": False, "spoken": False}
    assert speech.spoken == []

    state["enabled"] = True
    reply = handler({"command": "speak", "text": "Hi.", "if_enabled": True})
    assert reply == {"ok": True, "enabled": True, "spoken": True}
    assert speech.spoken == ["Hi."]


def test_unconditional_speak_ignores_toggle():
    handler, speech, _ = make_handler(enabled=False)

    handler({"command": "speak", "text": "Hi."})

    assert speech.spoken == ["Hi."]


def test_turning_off_stops_speech():
    handler, speech, state = make_handler(enabled=True)

    assert handler({"command": "set_enabled", "enabled": "toggle"})["enabled"] is False
    assert speech.stops == 1
    assert handler({"command": "set_enabled", "enabled": True})["enabled"] is True
    assert state["enabled"] is True


def test_invalid_requests_raise():
    handler, _, _ = make_handler()

    with pytest.raises(ValueError):
        handler({"command": "speak"})
    with pytest.raises(ValueError):
        handler({"command": "set_enabled", "enabled": "yes"})
    with pytest.raises(ValueError):
        handler({"command": "dance"})
