"""Install/sync entry (#3069): writes the shared /barb template to the Antigravity
workflow path, so this fleet host carries the same wrapper as every other CLI. Run
after editing the template in scripts/wrappers/agy_barb_template.py."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(_ROOT)]

from scripts.wrappers.agy_barb_template import render  # noqa: E402

PATHS = {"agy": _ROOT / ".agents" / "workflows" / "barb.md"}


def sync() -> list[Path]:
    written = []
    for provider, path in PATHS.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(provider), encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in sync():
        print(f"wrote {written_path}")
    sys.exit(0)
