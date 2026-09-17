from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path

from .constants import PROCESSORS_DIR


class ProcessorExistsError(FileExistsError):
    pass


@dataclass(frozen=True)
class ProcessorInfo:
    key: str
    display_name: str
    path: Path | None


@dataclass(frozen=True)
class ProcessorResult:
    text: str
    error: str | None = None


class ProcessorRegistry:
    def __init__(self, directory: Path = PROCESSORS_DIR) -> None:
        self.directory = Path(directory)

    def list_processors(self) -> list[ProcessorInfo]:
        processors = [ProcessorInfo("plain_text", "Plain Text", None)]
        if not self.directory.exists():
            return processors
        for path in sorted(self.directory.glob("*.py")):
            if path.name.startswith("_"):
                continue
            processors.append(
                ProcessorInfo(path.stem, self._display_name(path.stem), path)
            )
        return processors

    def create(self, name: str) -> ProcessorInfo:
        key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if not key:
            raise ValueError("Processor name must contain a letter or number")
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key}.py"
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(
                    '"""Custom Local Dictation processor."""\n\n\n'
                    "def process(text: str) -> str:\n"
                    '    """Transform and return the completed transcript."""\n'
                    "    return text\n"
                )
        except FileExistsError as exc:
            raise ProcessorExistsError(f"Processor already exists: {key}") from exc
        return ProcessorInfo(key, self._display_name(key), path)

    def apply(self, key: str, text: str) -> ProcessorResult:
        if key == "plain_text":
            return ProcessorResult(text)
        path = self.directory / f"{key}.py"
        try:
            if not path.is_file() or path.name.startswith("_"):
                raise FileNotFoundError(f"Processor not found: {key}")
            spec = importlib.util.spec_from_file_location(
                f"local_dictation_user_processor_{key}", path
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"Could not load processor: {key}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            process = getattr(module, "process", None)
            if not callable(process):
                raise TypeError("Processor must export process(text: str) -> str")
            result = process(text)
            if not isinstance(result, str):
                raise TypeError("Processor returned a non-string value")
            return ProcessorResult(result)
        except Exception as exc:
            return ProcessorResult(text, f"{type(exc).__name__}: {exc}")

    @staticmethod
    def _display_name(key: str) -> str:
        return re.sub(r"[_-]+", " ", key).title()
