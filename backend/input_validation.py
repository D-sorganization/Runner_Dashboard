"""Input validation helpers for workflow_dispatch handlers.

GitHub's `workflow_dispatch` API accepts an ``inputs`` mapping. Inputs in a
workflow file may be typed as ``string``, ``boolean``, ``number``, ``choice``,
or ``environment`` — the REST API accepts native JSON booleans and numbers
for those typed inputs and converts them to strings server-side. Without a
per-key length cap and a per-dict key-count cap, a malicious or buggy client
can ship a multi-megabyte payload that is serialized to a temp file and
forwarded to ``gh`` before GitHub itself rejects it with a 422.

This module centralizes the size and type checks so both
``/api/workflows/dispatch`` and ``/api/feature-requests/dispatch`` share a
single contract (DRY) and reject abuse cases before any I/O.

Constants are deliberately conservative — GitHub's own per-input-value cap
sits well above ``MAX_INPUT_VALUE_LENGTH``; the dashboard's tighter limit
exists to protect the dashboard itself, not GitHub.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

# ─── Module constants ─────────────────────────────────────────────────────────

MAX_INPUT_VALUE_LENGTH: int = 1000
"""Maximum number of characters permitted per input value (after str()-ification)."""

MAX_INPUT_KEYS: int = 20
"""Maximum number of keys permitted in an inputs mapping."""

# Native JSON value types accepted in workflow_dispatch inputs. GitHub Actions
# supports typed inputs (`boolean`, `number`, `choice`, `environment`,
# `string`); the REST API accepts native JSON booleans/numbers and converts
# them to strings server-side. We accept the same set and let the abuse-cap
# below enforce the size bound. `None` is rejected — workflow_dispatch has no
# null-input semantics. Subclasses of `bool` (which inherits from `int`) are
# handled correctly because `isinstance(True, int)` returns True and the
# resulting string is `"True"` / `"False"`, which matches GitHub's wire form.
_ALLOWED_VALUE_TYPES: tuple[type, ...] = (str, bool, int, float)


def validate_workflow_inputs(inputs: Any) -> dict[str, str]:
    """Validate a ``workflow_dispatch`` inputs payload.

    Pre-conditions
    --------------
    * ``inputs`` is ``None``, an empty mapping, or a mapping of string keys
      to ``str``/``bool``/``int``/``float`` values.
    * The mapping has at most ``MAX_INPUT_KEYS`` entries.
    * Every value's string representation has at most
      ``MAX_INPUT_VALUE_LENGTH`` characters.

    Post-conditions
    ---------------
    * Returns a fresh ``dict[str, str]`` containing the validated inputs,
      with non-string values coerced via ``str()`` (matching GitHub's wire
      form for typed workflow inputs).
    * Raises ``HTTPException`` with status 400 if any pre-condition fails.

    The function is intentionally side-effect-free so callers can apply it
    before any temp-file or subprocess work.
    """
    if inputs is None:
        return {}
    if not isinstance(inputs, Mapping):
        raise HTTPException(
            status_code=400,
            detail="inputs must be an object (mapping of string keys to string/boolean/number values)",
        )
    if len(inputs) > MAX_INPUT_KEYS:
        raise HTTPException(
            status_code=400,
            detail=(f"inputs has too many keys: {len(inputs)} > {MAX_INPUT_KEYS}. Reduce the number of input keys."),
        )

    validated: dict[str, str] = {}
    for key, value in inputs.items():
        if not isinstance(key, str):
            raise HTTPException(
                status_code=400,
                detail="inputs keys must be strings",
            )
        if value is None or not isinstance(value, _ALLOWED_VALUE_TYPES):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"inputs[{key!r}] must be a string, boolean, or number "
                    f"(GitHub workflow_dispatch supports those typed inputs); "
                    f"got {type(value).__name__}"
                ),
            )
        # Coerce to GitHub's wire form: bool -> "True"/"False", int/float -> str(...).
        coerced = str(value)
        if len(coerced) > MAX_INPUT_VALUE_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=(f"inputs[{key!r}] exceeds maximum length ({len(coerced)} > {MAX_INPUT_VALUE_LENGTH} chars)"),
            )
        validated[key] = coerced

    assert len(validated) <= MAX_INPUT_KEYS  # invariant
    return validated
