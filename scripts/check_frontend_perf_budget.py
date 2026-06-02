"""Validate the frontend performance budget contract.

The dashboard is still a no-build single-file SPA, so this check enforces the
checked-in mobile budget values and a gzip guardrail for the current artifact.
When route chunks land, the same budget file can be extended to inspect built
assets without weakening the target values.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BUDGET_PATH = ROOT / "frontend" / "perf-budget.json"
INDEX_PATH = ROOT / "frontend" / "index.html"
# Vite build output (see vite.config.ts: build.outDir = "../dist").
DIST_DIR = ROOT / "dist"
DIST_INDEX = DIST_DIR / "index.html"
DIST_ASSETS = DIST_DIR / "assets"

REQUIRED_TARGETS: dict[tuple[str, str], int] = {
    ("mobile_shell", "js_gzip_bytes"): 204800,
    ("mobile_shell", "css_gzip_bytes"): 51200,
    ("tab_chunk", "js_gzip_bytes"): 102400,
    ("mobile_lighthouse", "performance_min"): 90,
    ("field_timing", "inp_p75_ms"): 200,
    ("field_timing", "fcp_ms"): 1800,
}


def _gzip_size(text: str) -> int:
    return len(gzip.compress(text.encode("utf-8"), compresslevel=9, mtime=0))


def _inline_blocks(html: str, tag: str) -> str:
    if tag == "script":
        pattern = r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>"
    else:
        pattern = rf"<{tag}[^>]*>(.*?)</{tag}>"
    return "\n".join(re.findall(pattern, html, flags=re.IGNORECASE | re.DOTALL))


def load_budget(path: Path = BUDGET_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_budget_contract(budget: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if budget.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if budget.get("issue") != 200:
        errors.append("issue must be 200")

    budgets = budget.get("budgets")
    if not isinstance(budgets, dict):
        return errors + ["budgets must be an object"]

    for (section, key), expected in REQUIRED_TARGETS.items():
        actual = budgets.get(section, {}).get(key)
        if actual != expected:
            errors.append(f"budgets.{section}.{key} must remain {expected}, got {actual!r}")

    routes = budgets.get("mobile_lighthouse", {}).get("routes")
    expected_routes = ["Fleet", "Workflows", "Remediation", "Maxwell", "Reports"]
    if routes != expected_routes:
        errors.append(f"mobile_lighthouse.routes must be {expected_routes!r}")

    change_control = budget.get("change_control", {})
    if change_control.get("budget_increases_require_justification") is not True:
        errors.append("change_control.budget_increases_require_justification must be true")

    return errors


def measure_frontend(index_path: Path = INDEX_PATH) -> dict[str, int]:
    html = index_path.read_text(encoding="utf-8")
    return {
        "index_html_gzip_bytes": _gzip_size(html),
        "inline_js_gzip_bytes": _gzip_size(_inline_blocks(html, "script")),
        "inline_css_gzip_bytes": _gzip_size(_inline_blocks(html, "style")),
    }


def validate_interim_sizes(budget: dict[str, Any], sizes: dict[str, int]) -> list[str]:
    interim = budget.get("budgets", {}).get("interim_single_file", {})
    errors: list[str] = []
    for key, actual in sizes.items():
        limit = interim.get(key)
        if not isinstance(limit, int):
            errors.append(f"budgets.interim_single_file.{key} must be an integer")
            continue
        if actual > limit:
            errors.append(f"{key} is {actual} bytes gzip, over budget {limit}")
    return errors


# ---------------------------------------------------------------------------
# Real built-bundle enforcement (issue #831)
# ---------------------------------------------------------------------------


def _gzip_bytes(data: bytes) -> int:
    return len(gzip.compress(data, compresslevel=9, mtime=0))


def find_entry_chunk(dist_index: Path = DIST_INDEX) -> str | None:
    """Return the entry JS chunk filename referenced by the built index.html.

    Vite emits a single `<script type="module" src="/assets/index-*.js">` for
    the entry; that chunk plus its modulepreloaded vendor chunks are what loads
    on first paint. Returns just the basename (e.g. "index-Xn-sGiGC.js").
    """
    if not dist_index.exists():
        return None
    html = dist_index.read_text(encoding="utf-8")
    match = re.search(r'<script[^>]*\btype="module"[^>]*\bsrc="([^"]+\.js)"', html, flags=re.IGNORECASE)
    if not match:
        return None
    return match.group(1).rsplit("/", 1)[-1]


def measure_built_bundle(
    dist_assets: Path = DIST_ASSETS,
    dist_index: Path = DIST_INDEX,
    vendor_prefixes: tuple[str, ...] = ("vendor-",),
) -> dict[str, Any]:
    """Measure gzip sizes of the real Vite-built JS chunks.

    Splits chunks into: the entry chunk (loaded on first paint), shared vendor
    chunks (excluded from the per-chunk app budget), and async/app chunks
    (per-route/tab chunks — the lazily loaded legacy App lands here).
    """
    entry_name = find_entry_chunk(dist_index)
    js_files = sorted(dist_assets.glob("*.js")) if dist_assets.exists() else []

    entry_gzip = 0
    vendor: dict[str, int] = {}
    chunks: dict[str, int] = {}
    for path in js_files:
        name = path.name
        size = _gzip_bytes(path.read_bytes())
        if name == entry_name:
            entry_gzip = size
        elif any(name.startswith(p) for p in vendor_prefixes):
            vendor[name] = size
        else:
            chunks[name] = size

    return {
        "entry_chunk": entry_name,
        "entry_js_gzip_bytes": entry_gzip,
        "vendor_chunks": vendor,
        "app_chunks": chunks,
    }


def validate_built_bundle(budget: dict[str, Any], measured: dict[str, Any]) -> list[str]:
    """Enforce the real-bundle budget (issue #831).

    Preconditions: a Vite build must exist (dist/assets/*.js with an entry
    chunk). Postcondition: returns [] only when the entry chunk is under
    entry_js_gzip_bytes AND every non-vendor app chunk is under
    per_chunk_js_gzip_bytes.
    """
    cfg = budget.get("budgets", {}).get("built_bundle", {})
    entry_limit = cfg.get("entry_js_gzip_bytes")
    chunk_limit = cfg.get("per_chunk_js_gzip_bytes")
    errors: list[str] = []

    if not isinstance(entry_limit, int):
        errors.append("budgets.built_bundle.entry_js_gzip_bytes must be an integer")
    if not isinstance(chunk_limit, int):
        errors.append("budgets.built_bundle.per_chunk_js_gzip_bytes must be an integer")
    if errors:
        return errors

    if not measured.get("entry_chunk"):
        return [
            "no built bundle found: run `npm run build` so dist/assets/*.js exists "
            "before enforcing the real-bundle budget"
        ]

    entry_gzip = measured["entry_js_gzip_bytes"]
    if entry_gzip > entry_limit:
        errors.append(
            f"entry chunk {measured['entry_chunk']} is {entry_gzip} bytes gzip, "
            f"over entry budget {entry_limit} — the legacy App must stay lazy-loaded "
            f"(code-split) out of the entry chunk (issue #831)"
        )

    for name, size in sorted(measured.get("app_chunks", {}).items()):
        if size > chunk_limit:
            errors.append(f"app chunk {name} is {size} bytes gzip, over per-chunk budget {chunk_limit}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print measured sizes as JSON")
    parser.add_argument(
        "--bundle",
        action="store_true",
        help="enforce the real built-bundle budget against dist/assets/*.js (issue #831)",
    )
    args = parser.parse_args()

    budget = load_budget()

    if args.bundle:
        measured = measure_built_bundle()
        errors = validate_budget_contract(budget) + validate_built_bundle(budget, measured)
        if args.json:
            print(json.dumps({"bundle": measured, "errors": errors}, indent=2, sort_keys=True))
        if errors:
            for error in errors:
                print(f"frontend performance budget: {error}")
            return 1
        return 0

    sizes = measure_frontend()
    errors = validate_budget_contract(budget) + validate_interim_sizes(budget, sizes)
    if args.json:
        print(json.dumps({"sizes": sizes, "errors": errors}, indent=2, sort_keys=True))
    if errors:
        for error in errors:
            print(f"frontend performance budget: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
