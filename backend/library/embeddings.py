"""Passage embeddings via Ollama or deterministic test vectors (#2165 slice 7)."""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Callable
from typing import Any

import httpx
from staff.ollama_env import ollama_base_url

log = logging.getLogger("dashboard.library.embeddings")

EMBED_DIM = int(os.environ.get("LIBRARY_EMBED_DIM", "8"))
_WORD = re.compile(r"\w+", re.UNICODE)
_TEST_LEX: dict[str, int] = {
    "golf": 0,
    "hip": 1,
    "torque": 2,
    "downswing": 3,
    "biomechanics": 4,
    "clinical": 5,
    "rehab": 6,
    "flexor": 7,
}


def embed_provider() -> str:
    return os.environ.get("LIBRARY_EMBED_PROVIDER", "ollama").strip().lower()


def embed_model() -> str:
    return os.environ.get("LIBRARY_EMBED_MODEL", "nomic-embed-text").strip()


def deterministic_embedding_for_text(text: str, *, dim: int = EMBED_DIM) -> list[float]:
    vec = [0.0] * dim
    for word in _WORD.findall(text.lower()):
        idx = _TEST_LEX.get(word)
        if idx is not None and idx < dim:
            vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def embed_texts_ollama(texts: list[str], *, model: str | None = None, base_url: str | None = None) -> list[list[float]]:
    if not texts:
        return []
    url = f"{(base_url or ollama_base_url()).rstrip('/')}/api/embed"
    payload: dict[str, Any] = {"model": model or embed_model(), "input": texts}
    try:
        resp = httpx.post(url, json=payload, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        embeddings = data.get("embeddings")
        if not isinstance(embeddings, list):
            raise ValueError("ollama embed response missing embeddings")
        return [[float(x) for x in row] for row in embeddings]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("ollama embedding failed: %s", exc)
        raise


def passage_embedder() -> Callable[[int, str], list[float]]:
    if embed_provider() == "test":
        return lambda _pid, text: deterministic_embedding_for_text(text)
    model = embed_model()

    def _ollama(pid: int, text: str) -> list[float]:
        return embed_texts_ollama([text], model=model)[0]

    return _ollama


def query_embedding(text: str) -> list[float]:
    if embed_provider() == "test":
        return deterministic_embedding_for_text(text)
    return embed_texts_ollama([text], model=embed_model())[0]
