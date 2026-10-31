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
