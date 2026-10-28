"""Codex provider install/sync entry point for the rd-barb prompt and hook."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from clients.fleet.codex.barb_wrapper import install  # noqa: E402, I001


if __name__ == "__main__":
    sys.stdout.write(f"{install()}\n")
