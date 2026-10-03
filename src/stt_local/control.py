"""Local control socket that lets other programs, such as Claude Code hooks,
ask the running app to speak. Keep this module free of heavy imports: the
command-line client loads it on every hook call."""

from __future__ import annotations

import json
import os
import socket
import socketserver
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .constants import CONTROL_SOCKET

MAX_MESSAGE_BYTES = 1_000_000


class ControlSocketInUse(RuntimeError):
    pass


def _socket_is_live(path: Path) -> bool:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        try:
            probe.connect(str(path))
        except OSError:
            return False
    return True


class ControlServer:
    """Answers one JSON request line per connection with one JSON reply line."""

    def __init__(
        self, handler: Callable[[dict[str, Any]], dict[str, Any]], path: Path = CONTROL_SOCKET
    ) -> None:
        self.handler = handler
        self.path = Path(path)
        self._server: socketserver.ThreadingUnixStreamServer | None = None

    def start(self) -> None:
        if self.path.exists():
            if _socket_is_live(self.path):
                raise ControlSocketInUse(
                    f"Another STT Local instance is listening on {self.path}"
                )
            self.path.unlink()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handler = self.handler

        class RequestHandler(socketserver.StreamRequestHandler):
            def handle(self) -> None:
                line = self.rfile.readline(MAX_MESSAGE_BYTES)
                if not line:
                    return  # A liveness probe that connected and hung up.
                try:
                    request = json.loads(line)
                    if not isinstance(request, dict):
                        raise ValueError("The request must be a JSON object")
                    reply = handler(request)
                except Exception as exc:
                    reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                self.wfile.write(json.dumps(reply).encode() + b"\n")

        server = socketserver.ThreadingUnixStreamServer(str(self.path), RequestHandler)
        server.daemon_threads = True
        os.chmod(self.path, 0o600)
        self._server = server
        threading.Thread(
            target=server.serve_forever, daemon=True, name="stt-local-control"
        ).start()

    def close(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self.path.unlink(missing_ok=True)


def send_command(
    request: dict[str, Any], *, path: Path = CONTROL_SOCKET, timeout: float = 2.0
) -> dict[str, Any] | None:
    """Send one request to the running app; None when it is not running."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        try:
            client.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            return None
        client.sendall(json.dumps(request).encode() + b"\n")
        with client.makefile("rb") as reply:
            line = reply.readline(MAX_MESSAGE_BYTES)
    if not line:
        return None
    return json.loads(line)


class CommandHandler:
    """Maps control requests onto the speech manager and the saved toggle."""

    def __init__(
        self,
        *,
        speech: Any,
        is_enabled: Callable[[], bool],
        set_enabled: Callable[[bool], None],
    ) -> None:
        self.speech = speech
        self.is_enabled = is_enabled
        self.set_enabled = set_enabled

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        command = request.get("command")
        if command == "speak":
            text = request.get("text")
            if not isinstance(text, str):
                raise ValueError("speak needs a text string")
            spoken = bool(text.strip()) and (
                not request.get("if_enabled") or self.is_enabled()
            )
            if spoken:
                self.speech.speak(text)
            return self._reply(spoken=spoken)
        if command == "stop":
            self.speech.stop()
            return self._reply()
        if command == "status":
            return self._reply()
        if command == "set_enabled":
            enabled = request.get("enabled")
            if enabled == "toggle":
                enabled = not self.is_enabled()
            if not isinstance(enabled, bool):
                raise ValueError('enabled must be true, false or "toggle"')
            self.set_enabled(enabled)
            if not enabled:
                self.speech.stop()
            return self._reply()
        raise ValueError(f"Unknown command: {command}")

    def _reply(self, **extra: Any) -> dict[str, Any]:
        return {"ok": True, "enabled": self.is_enabled(), **extra}
