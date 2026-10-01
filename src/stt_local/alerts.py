from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from typing import Any, TextIO

# A bare Python interpreter has no bundle identifier, so macOS gives it no
# NSUserNotificationCenter and rumps.notification() raises. osascript posts
# the notification on its own behalf, which works from a LaunchAgent.
_NOTIFY_SCRIPT = (
    "on run argv\n"
    "display notification (item 2 of argv) with title (item 1 of argv)\n"
    "end run"
)


def notification_command(title: str, message: str) -> list[str]:
    return ["osascript", "-e", _NOTIFY_SCRIPT, f"STT Local: {title}", message]


def menu_error_title(title: str, message: str, limit: int = 120) -> str:
    text = f"{title}: {message}" if message else title
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def report_error(
    title: str,
    message: str,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    stream: TextIO | None = None,
) -> None:
    """Log an error and show it as a notification; never raises."""
    stream = stream if stream is not None else sys.stderr
    try:
        print(f"STT Local error: {title}: {message}", file=stream, flush=True)
    except Exception:
        pass
    try:
        popen(
            notification_command(title, message),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
