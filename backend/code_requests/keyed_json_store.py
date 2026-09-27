"""A node-local JSON file of pydantic models keyed by id (shared by plan sessions and pipelines)."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Generic, TypeVar

from config_schema import atomic_write_json
from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


class KeyedJsonStore(Generic[M]):
    """Reads and writes ``{key: model}`` in one JSON file; an unreadable file reads as empty."""

    def __init__(self, path: Path, model: type[M], key: Callable[[M], str]) -> None:
        self.path = path
        self._model = model
        self._key = key
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> M | None:
        with self._lock:
            raw = self._read().get(key)
        return self._model.model_validate(raw) if isinstance(raw, dict) else None

    def save(self, item: M) -> M:
        with self._lock:
            data = self._read()
            data[self._key(item)] = item.model_dump(mode="json")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, data)
        return item
