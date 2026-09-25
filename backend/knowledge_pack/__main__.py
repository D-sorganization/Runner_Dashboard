# Vendored from Tools (commit 09ff428af314969363f8908dcebafb84ddd7a3ef)
# Path in Tools: src/shared/python/ai/knowledge/__main__.py
# Part of D-sorganization/Repository_Management#1772 (K0 / K2, Runner_Dashboard#1479)
"""Entry point for ``python -m shared.python.ai.knowledge``."""

from __future__ import annotations

import sys

from .cli import main

sys.exit(main())
