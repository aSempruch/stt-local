"""`stt-local-speech`: speak through the running app, and the Claude Code hooks
that read replies aloud. Every hook path exits 0 so Claude Code is never
blocked or shown an error because STT Local is not running."""

from __future__ import annotations

import argparse
import json
import re
import sys
from typing import Any

from .control import send_command

VOICE_MODE_CONTEXT = (
    "Voice mode is on: the user hears your replies through text-to-speech. "
    "Write the reply for the terminal as usual, then end it with one "
    "<spoken>...</spoken> block of one to three short, conversational "
    "sentences meant to be heard: what you did or found, and anything you "
    "need from the user. Inside the block use no markdown, code, file paths, "
    "URLs, symbols or lists; say things the way a person would aloud."
)

FALLBACK_LIMIT = 400

_SPOKEN_BLOCK = re.compile(r"<spoken>(.*?)</spoken>", re.DOTALL | re.IGNORECASE)
_FENCED_CODE = re.compile(r"^\s*(```|~~~).*?^\s*\1[^\n]*$", re.DOTALL | re.MULTILINE)
_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_LINE_MARKER = re.compile(r"^\s*(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)", re.MULTILINE)
_EMPHASIS = re.compile(r"(\*\*|__|\*|`)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def plain_speech(text: str) -> str:
    """Reduce Markdown to text that sounds natural when read aloud."""
    text = _FENCED_CODE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("a link", text)
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("|")]
    text = _LINE_MARKER.sub("", "\n".join(lines))
    text = _EMPHASIS.sub("", text)
    return " ".join(text.split())


def first_paragraph(text: str, limit: int = FALLBACK_LIMIT) -> str:
    """The opening prose of a reply that has no spoken block, cut at a
    sentence boundary so long replies are not read in full."""
    text = _FENCED_CODE.sub("\n\n", text)
    for paragraph in re.split(r"\n\s*\n", text):
        if paragraph.lstrip().startswith(("#", "|")):
            continue
        spoken = plain_speech(paragraph)
        if not spoken:
            continue
        if len(spoken) <= limit:
            return spoken
        kept = ""
        for sentence in _SENTENCE_END.split(spoken):
            if kept and len(kept) + len(sentence) + 1 > limit:
                break
            kept = f"{kept} {sentence}".strip()
        return kept[:limit]
    return ""


def spoken_text(reply: str) -> str:
    blocks = _SPOKEN_BLOCK.findall(reply)
    if blocks:
        return plain_speech(blocks[-1])
    return first_paragraph(reply)


def _send(request: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return send_command(request)
    except (OSError, ValueError):
        return None


def _read_hook_input() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def claude_stop_hook() -> int:
    reply = _read_hook_input().get("last_assistant_message") or ""
    text = spoken_text(str(reply))
    if text:
        _send({"command": "speak", "text": text, "if_enabled": True})
    return 0


def claude_prompt_hook() -> int:
    _read_hook_input()
    # A new prompt makes the previous reply's speech stale.
    status = _send({"command": "stop"})
    if status and status.get("enabled"):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": VOICE_MODE_CONTEXT,
                    }
                }
            )
        )
    return 0


def _report(reply: dict[str, Any] | None) -> int:
    if reply is None:
        print("STT Local is not running", file=sys.stderr)
        return 1
    if not reply.get("ok"):
        print(reply.get("error", "Request failed"), file=sys.stderr)
        return 1
    print("Reading Claude Code replies: " + ("on" if reply.get("enabled") else "off"))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="stt-local-speech",
        description="Speak through the running STT Local app.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    say = commands.add_parser("say", help="speak text, or standard input")
    say.add_argument("text", nargs="*")
    commands.add_parser("stop", help="stop speaking")
    commands.add_parser("status", help="show whether Claude Code replies are read")
    for name in ("on", "off", "toggle"):
        commands.add_parser(name, help=f"turn reading Claude Code replies {name}")
    commands.add_parser("claude-stop-hook", help="Claude Code Stop hook")
    commands.add_parser("claude-prompt-hook", help="Claude Code UserPromptSubmit hook")
    args = parser.parse_args(argv)

    if args.command == "claude-stop-hook":
        return claude_stop_hook()
    if args.command == "claude-prompt-hook":
        return claude_prompt_hook()
    if args.command == "say":
        text = " ".join(args.text) if args.text else sys.stdin.read()
        return _report(_send({"command": "speak", "text": text}))
    if args.command in {"on", "off", "toggle"}:
        enabled: Any = {"on": True, "off": False, "toggle": "toggle"}[args.command]
        return _report(_send({"command": "set_enabled", "enabled": enabled}))
    return _report(_send({"command": args.command}))


if __name__ == "__main__":
    raise SystemExit(main())
