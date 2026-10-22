from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT = Path(__file__).resolve().parents[2] / "config" / "library" / "collections.yml"


@dataclass(frozen=True)
class CollectionsConfig:
    repos: dict[str, frozenset[str]]
    topic_folders: dict[str, frozenset[str]]
    excluded_folders: frozenset[str]

    def folder_to_topics(self, folder: str) -> frozenset[str]:
        out: set[str] = set()
        for topic, folders in self.topic_folders.items():
            if folder in folders:
                out.add(topic)
        return frozenset(out)

    def validate_topic(self, topic: str) -> None:
        if topic not in self.topic_folders:
            raise ValueError(f"unknown topic: {topic}")

    def validate_repo(self, repo: str) -> None:
        if repo not in self.repos:
            raise ValueError(f"unknown repo: {repo}")

    def repo_topics(self, repo: str) -> frozenset[str]:
        return self.repos[repo]


def collections_path() -> Path:
    raw = os.environ.get("LIBRARY_COLLECTIONS_PATH", "").strip()
    return Path(raw) if raw else _DEFAULT


def load_collections(path: Path | None = None) -> CollectionsConfig:
    src = Path(path) if path is not None else collections_path()
    data = yaml.safe_load(src.read_text(encoding="utf-8")) or {}
    repos = {k: frozenset(v.get("topics") or []) for k, v in (data.get("repos") or {}).items()}
    topics = {k: frozenset(v.get("folders") or []) for k, v in (data.get("topics") or {}).items()}
    excluded = frozenset(data.get("excluded_folders") or [])
    return CollectionsConfig(repos=repos, topic_folders=topics, excluded_folders=excluded)


def topics_for_rel_path(rel: str, cfg: CollectionsConfig) -> list[str]:
    parts = Path(rel).parts
    if not parts or parts[0] in cfg.excluded_folders:
        return []
    return sorted(cfg.folder_to_topics(parts[0]))
