from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .constants import (
    APPLICATION_SUPPORT_DIR,
    DEFAULT_DOUBLE_TAP_SECONDS,
    DEFAULT_IDLE_UNLOAD_SECONDS,
    DEFAULT_LONG_PRESS_SECONDS,
    SPEECH_VOICE,
)


@dataclass(frozen=True)
class AppConfig:
    selected_processor: str = "plain_text"
    idle_unload_seconds: float = DEFAULT_IDLE_UNLOAD_SECONDS
    bias_prompt: str = ""
    speech_voice: str = SPEECH_VOICE
    double_tap_seconds: float = DEFAULT_DOUBLE_TAP_SECONDS
    long_press_seconds: float = DEFAULT_LONG_PRESS_SECONDS


class ConfigStore:
    def __init__(self, directory: Path = APPLICATION_SUPPORT_DIR) -> None:
        self.directory = Path(directory)
        self.path = self.directory / "config.json"

    def load(self) -> AppConfig:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            selected = data["selected_processor"]
            idle_seconds = data["idle_unload_seconds"]
            bias_prompt = data.get("bias_prompt", "")
            voice = data.get("speech_voice", SPEECH_VOICE)
            double_tap = data.get("double_tap_seconds", DEFAULT_DOUBLE_TAP_SECONDS)
            long_press = data.get("long_press_seconds", DEFAULT_LONG_PRESS_SECONDS)
            if not isinstance(selected, str) or not selected:
                raise ValueError("selected_processor must be a non-empty string")
            if not isinstance(idle_seconds, (int, float)) or idle_seconds <= 0:
                raise ValueError("idle_unload_seconds must be positive")
            if not isinstance(bias_prompt, str):
                raise ValueError("bias_prompt must be a string")
            if not isinstance(voice, str) or not voice:
                raise ValueError("speech_voice must be a non-empty string")
            for name, value in (
                ("double_tap_seconds", double_tap),
                ("long_press_seconds", long_press),
            ):
                if not isinstance(value, (int, float)) or value <= 0:
                    raise ValueError(f"{name} must be positive")
            return AppConfig(
                selected,
                float(idle_seconds),
                bias_prompt,
                voice,
                float(double_tap),
                float(long_press),
            )
        except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return AppConfig()

    def save(self, config: AppConfig) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(asdict(config), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
