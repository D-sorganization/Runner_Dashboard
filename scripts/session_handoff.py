#!/usr/bin/env python3
"""Export reviewed task context and import it as a new-session prompt.

No provider process, credentials, native session state or network is involved.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

MAX_BYTES = 262144
PROVIDERS = ("codex", "claude", "antigravity", "cursor", "cline", "ollama")
PAYLOAD_FIELDS = {"work_item", "context", "decisions", "evidence", "next_steps"}
PACKAGE_FIELDS = {"format", "version", "source_provider", "payload"}


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_payload(value: Any) -> dict[str, Any]:
    """Reject unknown fields and invalid structures at the import boundary."""
    if not isinstance(value, dict) or set(value) != PAYLOAD_FIELDS:
        raise ValueError("expected work_item, context, decisions, evidence and next_steps")
    if not _text(value["work_item"]) or not _text(value["context"]):
        raise ValueError("work_item and context must be nonempty text")
    for field in ("decisions", "next_steps"):
        if not isinstance(value[field], list) or not all(_text(item) for item in value[field]):
            raise ValueError("decisions and next_steps must be lists of nonempty text")
    evidence = value["evidence"]
    if not isinstance(evidence, list) or not all(
        isinstance(item, dict) and set(item) == {"ref", "summary"} and all(_text(v) for v in item.values())
        for item in evidence
    ):
        raise ValueError("evidence must contain ref/summary text pairs")
    return value


def validate_package(value: Any) -> dict[str, Any]:
    """Accept only the supported portable format, never native session dumps."""
    if not isinstance(value, dict) or set(value) != PACKAGE_FIELDS:
        raise ValueError("invalid package fields")
    if value["format"] != "rd-task-handoff" or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported package format or version")
    if value["source_provider"] not in PROVIDERS:
        raise ValueError("unsupported source provider")
    validate_payload(value["payload"])
    return value


def _read(path: Path) -> Any:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("input exceeds 256 KiB")
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("input must be UTF-8 JSON") from exc


def _write(path: Path, value: dict[str, Any]) -> None:
    raw = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("output exceeds 256 KiB")
    # Exclusive creation avoids replacing another handoff or following a symlink.
    with path.open("x", encoding="utf-8") as stream:
        stream.write(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="wrap reviewed task context in a portable package")
    export.add_argument("--source-provider", choices=PROVIDERS, required=True)
    import_command = commands.add_parser("import", help="prepare a context prompt for a new provider session")
    import_command.add_argument("--provider", choices=PROVIDERS, required=True)
    for command in (export, import_command):
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        value = _read(args.input)
        if args.command == "export":
            result = {
                "format": "rd-task-handoff",
                "version": 1,
                "source_provider": args.source_provider,
                "payload": validate_payload(value),
            }
        else:
            package = validate_package(value)
            result = {
                "provider": args.provider,
                "handoff": package,
                "prompt": (
                    "Resume this work item using the following task context; verify evidence before relying on it.\n"
                    + json.dumps(package["payload"], ensure_ascii=False, sort_keys=True)
                ),
            }
        _write(args.output, result)
    except (OSError, ValueError, RecursionError):
        # Never echo task contents or filesystem paths into shared logs.
        sys.stderr.write("handoff: invalid input or output unavailable; check the documented schema and size limit\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
