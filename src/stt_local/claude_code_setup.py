"""Installs (or removes) the Claude Code side of voice mode: the `/voice-mode`
command file and the two hooks in the user's settings.json, pointing at this
checkout's `stt-local-speech`. Other settings and hooks are left untouched, and
re-running replaces this checkout's hooks instead of adding duplicates."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

COMMAND_SOURCE = (
    Path(__file__).resolve().parents[2] / "integrations" / "claude-code" / "voice-mode.md"
)
HOOKS = {
    "Stop": "claude-stop-hook",
    "UserPromptSubmit": "claude-prompt-hook",
}
HOOK_TIMEOUT_SECONDS = 5
_OUR_HOOK = re.compile(r"stt-local-speech['\"]?\s+claude-(?:stop|prompt)-hook\b")


class SetupError(RuntimeError):
    pass


def claude_config_dir() -> Path:
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def speech_executable() -> Path:
    return Path(sys.executable).parent / "stt-local-speech"


def _is_ours(hook: Any) -> bool:
    return isinstance(hook, dict) and bool(_OUR_HOOK.search(str(hook.get("command", ""))))


def _without_our_hooks(groups: list[Any]) -> list[Any]:
    kept = []
    for group in groups:
        if isinstance(group, dict) and isinstance(group.get("hooks"), list):
            hooks = [hook for hook in group["hooks"] if not _is_ours(hook)]
            if not hooks:
                continue
            group = {**group, "hooks": hooks}
        kept.append(group)
    return kept


def update_settings(
    settings: dict[str, Any], executable: Path | None, *, install: bool
) -> dict[str, Any]:
    """Settings with this app's hooks removed and, when installing, re-added."""
    settings = dict(settings)
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise SetupError('"hooks" in settings.json is not an object')
    hooks = dict(hooks)
    for event, subcommand in HOOKS.items():
        groups = hooks.get(event, [])
        if not isinstance(groups, list):
            raise SetupError(f'"hooks.{event}" in settings.json is not a list')
        groups = _without_our_hooks(groups)
        if install:
            assert executable is not None
            groups.append(
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{shlex.quote(str(executable))} {subcommand}",
                            "timeout": HOOK_TIMEOUT_SECONDS,
                        }
                    ]
                }
            )
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    if hooks:
        settings["hooks"] = hooks
    else:
        settings.pop("hooks", None)
    return settings


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text() or "{}")
    except ValueError as exc:
        raise SetupError(f"{path} is not valid JSON ({exc}); fix it and run again") from exc
    if not isinstance(settings, dict):
        raise SetupError(f"{path} does not contain a JSON object")
    return settings


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.stt-local-tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def _write_settings(
    path: Path, settings: dict[str, Any], original: dict[str, Any]
) -> bool:
    """Write settings if they changed, keeping the previous file as .bak."""
    if path.exists():
        if settings == original:
            return False
        path.with_name(f"{path.name}.bak").write_text(path.read_text())
    _write_text(path, json.dumps(settings, indent=2) + "\n")
    return True


def install(
    config_dir: Path | None = None,
    executable: Path | None = None,
    command_source: Path = COMMAND_SOURCE,
) -> list[str]:
    config_dir = config_dir or claude_config_dir()
    executable = executable or speech_executable()
    if not executable.exists():
        raise SetupError(
            f"{executable} does not exist; run ./scripts/install-launch-agent.sh first"
        )
    if not command_source.exists():
        raise SetupError(f"{command_source} is missing from this checkout")
    settings_path = config_dir / "settings.json"
    original = _read_settings(settings_path)
    settings = update_settings(original, executable, install=True)
    _write_text(config_dir / "commands" / "voice-mode.md", command_source.read_text())
    changed = _write_settings(settings_path, settings, original)
    return [
        f"Installed the /voice-mode command in {config_dir / 'commands'}",
        f"{'Added' if changed else 'Kept'} the Stop and UserPromptSubmit hooks in "
        f"{settings_path}, running {executable}",
    ]


def uninstall(config_dir: Path | None = None) -> list[str]:
    config_dir = config_dir or claude_config_dir()
    settings_path = config_dir / "settings.json"
    messages = []
    if settings_path.exists():
        original = _read_settings(settings_path)
        settings = update_settings(original, None, install=False)
        if _write_settings(settings_path, settings, original):
            messages.append(f"Removed the STT Local hooks from {settings_path}")
    command = config_dir / "commands" / "voice-mode.md"
    if command.exists():
        command.unlink()
        messages.append(f"Removed {command}")
    return messages or ["Nothing to remove"]
