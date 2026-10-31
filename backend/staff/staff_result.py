"""Shared ``STAFF_RESULT`` JSON Schema (RM epic #2560 slice 9, RD #3619).

Today every unattended run reports its outcome as a free-text
``STAFF_RESULT: <summary>`` line that downstream code greps and regexes out
of the final message (see ``verification.py``, ``review.py``,
``consolidation.py``, ``run_link.py``). This schema lets a CLI that supports
native structured output (Claude's ``--json-schema``, Codex's
``--output-schema``) validate its final answer against the same shape
instead of relying on prefix matching.

It is intentionally additive: nothing is passed by default, and the
existing free-text convention keeps working unchanged until a caller opts
in by passing ``output_schema`` to :meth:`ProviderAdapter.build_command`.
"""

from __future__ import annotations

import json
import pathlib

STAFF_RESULT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One-line outcome, equivalent to today's 'STAFF_RESULT: <summary>' text.",
        },
    },
    "required": ["summary"],
    "additionalProperties": False,
}


def apply_output_schema(provider_id: str, out: list[str], workdir: str, output_schema: dict) -> None:
    """Append the native structured-output flag for ``provider_id``, if it has one.

    Claude-style providers get the schema inline (``--json-schema``); codex-style
    providers get it written to a file under ``workdir`` and passed by path
    (``--output-schema``), since their CLI has no inline-JSON flag. A provider
    with neither flag is left untouched.
    """
    if provider_id in ("claude", "claude-ollama", "anthropic-api"):
        out.extend(["--json-schema", json.dumps(output_schema)])
    elif provider_id in ("codex", "ollama"):
        schema_path = str(pathlib.Path(workdir) / ".staff_result_schema.json")
        pathlib.Path(schema_path).write_text(json.dumps(output_schema))
        out.extend(["--output-schema", schema_path])
