from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .constants import APPLICATION_SUPPORT_DIR, DEFAULT_IDLE_UNLOAD_SECONDS


@dataclass(frozen=True)
class AppConfig:
    selected_processor: str = "plain_text"
    idle_unload_seconds: float = DEFAULT_IDLE_UNLOAD_SECONDS


class ConfigStore:
    def __init__(self, directory: Path = APPLICATION_SUPPORT_DIR) -> None:
        self.directory = Path(directory)
        self.path = self.directory / "config.json"

    def load(self) -> AppConfig:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            selected = data["selected_processor"]
            idle_seconds = data["idle_unload_seconds"]
            if not isinstance(selected, str) or not selected:
                raise ValueError("selected_processor must be a non-empty string")
            if not isinstance(idle_seconds, (int, float)) or idle_seconds <= 0:
                raise ValueError("idle_unload_seconds must be positive")
            return AppConfig(selected, float(idle_seconds))
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
