# Vendored from D-sorganization/Repository_Management scripts/fork_pr_guard_analysis.py (RM#2338 @ 3436bd9d).
# Re-sync from upstream; do not fork. No local changes except ruff format at this repository's line length.
"""Condition and head-checkout analysis for ``fork_pr_runner_guard`` (RM#1996).

Pure helpers over workflow expressions and steps; no I/O. Split out of the
guard so each module stays small. Vendor this module and ``fork_pr_context_refs``
next to the guard; the context scanner is a required dependency.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING or __package__:
    from scripts.fork_pr_context_refs import (
        expression_string_literals,
        outside_actions_expressions,
        reads_head_context,
        render_literal_interpolations,
    )
else:
    from fork_pr_context_refs import (
        expression_string_literals,
        outside_actions_expressions,
        reads_head_context,
        render_literal_interpolations,
    )

#: Text that checks out or fetches a pull request's head.
HEAD_REF_PATTERNS = (
    re.compile(r"refs/pull/"),
    re.compile(r"pull/(\$\{\{[^}]*\}\}|[^\s/]+)/(head|merge)"),
    re.compile(r"gh\s+pr\s+checkout"),
)


#: A step restricted to this event never runs on a privileged trigger.
PULL_REQUEST_ONLY = "github.event_name == 'pull_request'"
#: Job-level conditions that make a privileged-event job same-repo only. Each is
#: accepted in either operand order and only when ANDed into the job ``if:``.
SAME_REPO_CONDITIONS = (
    "github.event.pull_request.head.repo.full_name == github.repository",
    "github.event.workflow_run.head_repository.full_name == github.repository",
)

#: ``ctx['key']`` / ``ctx["key"]`` property access in a GitHub expression.
_BRACKET_PROPERTY = re.compile(r"\[\s*['\"]([A-Za-z_][\w-]*)['\"]\s*\]")


def dotted(text: str) -> str:
    """Rewrite bracket property access as dots, so ``head['sha']`` reads ``head.sha``.

    Postcondition: dotted-only text is returned unchanged.
    """
    return _BRACKET_PROPERTY.sub(r".\1", text)


def normalize(expression: str) -> str:
    """Collapse whitespace and strip ``${{ }}`` and redundant outer parens."""
    text = " ".join(expression.split())
    if text.startswith("${{") and text.endswith("}}"):
        text = text[3:-2].strip()
    while text.startswith("(") and _matching_paren(text) == len(text) - 1:
        text = text[1:-1].strip()
    return text


def _matching_paren(text: str) -> int:
    """Return the index of the paren closing ``text[0]``, or -1."""
    depth = 0
    in_quote = False
    for index, char in enumerate(text):
        if char == "'":
            in_quote = not in_quote
        elif not in_quote and char == "(":
            depth += 1
        elif not in_quote and char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def split_top_level(expression: str, operator: str) -> list[str]:
    """Split ``expression`` on ``operator`` outside quotes and parentheses."""
    parts: list[str] = []
    depth = 0
    in_quote = False
    start = 0
    index = 0
    while index < len(expression):
        char = expression[index]
        if char == "'":
            in_quote = not in_quote
        elif not in_quote and char == "(":
            depth += 1
        elif not in_quote and char == ")":
            depth -= 1
        elif not in_quote and depth == 0 and expression.startswith(operator, index):
            parts.append(expression[start:index].strip())
            index += len(operator)
            start = index
            continue
        index += 1
    parts.append(expression[start:].strip())
    return parts


def requires_conjunct(condition: Any, conjunct: str) -> bool:
    """True when ``condition`` is ``conjunct`` or ANDs it at the top level.

    A top-level ``||`` anywhere would let another branch bypass the conjunct,
    so it disqualifies the condition.
    """
    if not isinstance(condition, str):
        return False
    text = normalize(condition)
    wanted = normalize(conjunct)
    if text == wanted:
        return True
    if len(split_top_level(text, "||")) != 1:
        return False
    return any(normalize(part) == wanted for part in split_top_level(text, "&&"))


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def reads_head(text: str, aliases: Iterable[str] = ()) -> bool:
    """Combine textual head/alias checks with quote-aware context recognition.

    Pass original text to the context scanner: normalization must not rewrite
    bracket-looking text inside expression string literals.
    """
    normalized = dotted(text)
    names = tuple(aliases)
    patterns = (*HEAD_REF_PATTERNS, *(_alias_reference(name) for name in names))
    if any(pattern.search(normalized) for pattern in patterns):
        return True
    native = dotted(outside_actions_expressions(text))
    native_patterns = tuple(_native_alias_reference(name) for name in names)
    if any(pattern.search(native) for pattern in native_patterns):
        return True
    rendered = dotted(render_literal_interpolations(text)).strip()
    if any(pattern.search(rendered) and not pattern.fullmatch(rendered) for pattern in native_patterns):
        return True
    for literal in expression_string_literals(text):
        source = dotted(literal).strip()
        # Bare env.NAME values are metadata; containing script programs can read it.
        if any(pattern.search(source) and not pattern.fullmatch(source) for pattern in native_patterns):
            return True
    return reads_head_context(text, names)


def head_ref_aliases(*scopes: Any, known: Iterable[str] = ()) -> set[str]:
    """Return env names, across ``scopes``, whose value reads the PR head.

    Precondition: ``scopes`` are ordered outermost first (workflow, job, step).
    A name is an alias when its value references a head literal, a ``known``
    alias, or an alias from an *enclosing* scope (``HEAD`` -> ``REF:
    ${{ env.HEAD }}`` one level down). Entries of one ``env`` map never resolve
    against each other: GitHub evaluates ``env.X`` inside an env map against
    the enclosing scopes only. An inner scope that redefines an outer alias
    does not clear it (fail-closed).
    Postcondition: the result contains every name in ``known``.
    """
    names: set[str] = {str(n) for n in known}
    for scope in scopes:
        env = scope.get("env") if isinstance(scope, dict) else None
        if not isinstance(env, dict):
            continue
        names |= {str(name) for name, value in env.items() if reads_head("\n".join(_strings(value)), names)}
    return names


def _alias_reference(name: str) -> re.Pattern[str]:
    """Match a shell reference to env var ``name``.

    PowerShell's env provider and Windows env keys are case-insensitive. Cover
    braced and unbraced forms; POSIX shell names remain exact. Actions references
    use the context scanner instead of a separate alias-expression regex.
    """
    n = re.escape(name)
    return re.compile(rf"\$\{{?{n}(?!\w)|(?i:\$\{{?env:{n}(?!\w))")


def _native_alias_reference(name: str) -> re.Pattern[str]:
    """Preserve native env.NAME reads, including normalized Node bracket access.

    Only known head aliases count. Script programs retain the generic env.NAME
    fallback, including a local env binding; this is not general data flow.
    """
    # Windows native environment keys are case-insensitive; fail closed across OSes.
    return re.compile(rf"\benv\s*\.\s*(?i:{re.escape(name)})(?!\w)")


def _swap_operands(equality: str) -> str:
    """Return ``a == b`` as ``b == a``.

    Precondition: ``equality`` contains exactly one ``==``.
    """
    assert equality.count("==") == 1, equality
    left, right = (side.strip() for side in equality.split("=="))
    return f"{right} == {left}"


def same_repo_conditioned(condition: Any) -> bool:
    """True when ``condition`` ANDs a same-repo head check at the top level.

    Preconditions: none; non-string input is simply not a condition.
    Postconditions: True only for :data:`SAME_REPO_CONDITIONS` in either operand
    order, alone or as a top-level ``&&`` conjunct. A top-level ``||`` (or any
    form not understood) returns False, so the job is not exempted.
    """
    if not isinstance(condition, str):
        return False
    text = dotted(condition)
    return any(
        requires_conjunct(text, form) for wanted in SAME_REPO_CONDITIONS for form in (wanted, _swap_operands(wanted))
    )


def _checkout_action_reads_head(inputs: Any, aliases: Iterable[str]) -> bool:
    """True when an ``actions/checkout`` ``with:`` names the head in ref/repository.

    Precondition: ``inputs`` is the step's ``with:`` value (any type).
    Postcondition: other inputs (``path``, ``token``...) never count.
    """
    if not isinstance(inputs, dict):
        return False
    # The runner upper-cases input keys (INPUT_REF), so ``REF:`` is ``ref:``.
    named = {str(key).lower(): value for key, value in inputs.items()}
    text = "\n".join(_strings([named.get("ref"), named.get("repository")]))
    return reads_head(text, aliases)


def _is_checkout_action(uses: Any) -> bool:
    """True only for ``actions/checkout`` itself (any ref, any letter case).

    Owner/repo in ``uses:`` are case-insensitive on GitHub. A prefix match would
    also exempt look-alikes such as ``actions/checkout-wrapper``, which get no
    per-input allowlist.
    """
    if not isinstance(uses, str):
        return False
    return uses.split("@", 1)[0].strip().lower() == "actions/checkout"


# Step keys that never hand data to the command or action: ``if`` is a gate
# (handled by the caller) and ``name`` is display text. Every other key,
# known or not, is scanned (fail-closed).
_INERT_STEP_KEYS = frozenset({"if", "name"})


def _step_checks_out_head(step: dict[str, Any], aliases: set[str]) -> bool:
    """Return whether one step reads the PR head (fail-closed).

    Postcondition: an ``actions/checkout`` step counts only when its ``ref`` or
    ``repository`` input names the head (``token``/``path`` are not code). Any
    other step that reads the head anywhere (``run``, ``env``, action inputs)
    counts: a helper script or action handed the head can fetch and execute it,
    so no command is trusted as data-only. A job-level same-repo condition is
    the sanctioned exemption.
    """
    # Step-level env aliases (``env: REF: <head>`` or ``REF: ${{ env.HEAD }}``)
    # read the head as surely as workflow- or job-level ones do.
    step_aliases = head_ref_aliases(step, known=aliases)
    if _is_checkout_action(step.get("uses")):
        return _checkout_action_reads_head(step.get("with"), step_aliases)
    fields = {k: v for k, v in step.items() if k not in _INERT_STEP_KEYS}
    return reads_head("\n".join(_strings(fields)), step_aliases)


def head_checkout_steps(job: dict[str, Any], aliases: set[str]) -> list[str]:
    """Return the names of steps that check out PR head on any event.

    ``aliases`` are workflow- or job-level env names that hold a head ref; a
    step that references one reads the head as surely as a literal does.
    """
    found: list[str] = []
    steps = job.get("steps")
    for index, step in enumerate(steps if isinstance(steps, list) else []):
        if not isinstance(step, dict):
            continue
        if requires_conjunct(step.get("if"), PULL_REQUEST_ONLY):
            continue
        if _step_checks_out_head(step, aliases):
            found.append(str(step.get("name") or step.get("uses") or f"#{index}"))
    return found


def passes_head_ref(job: dict[str, Any], aliases: set[str]) -> bool:
    """Return whether a reusable-workflow call hands the PR head to its callee.

    A ``uses:`` job has no steps of its own; the callee may check out whatever
    ref its inputs name, so a head ref in ``with:`` is a head checkout.
    """
    if not isinstance(job.get("uses"), str):
        return False
    return reads_head("\n".join(_strings(job.get("with", {}))), aliases)
