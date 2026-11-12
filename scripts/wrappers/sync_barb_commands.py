"""Install/sync entry (#3067): writes the shared /barb template to every registered
provider path, so all fleet hosts carry the identical wrapper. Run after editing the
template in scripts/wrappers/barb_command_template.py."""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path[:0] = [str(_ROOT)]

from scripts.wrappers.barb_command_template import render  # noqa: E402

PATHS = {"claude": _ROOT / ".claude" / "commands" / "barb.md"}


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
