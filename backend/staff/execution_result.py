"""Post-run status classification from exit code, STAFF_RESULT, and transcript."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from staff.classifier import classify_run_failure
from staff.verification import remote_deliverable_present

if TYPE_CHECKING:
    from staff.store import RunRecord
    from staff.verification import PrProbe

_PHANTOM_NUDGE_SUMMARIES = frozenset(
    {
        "no repository task was specified",
        "no task was specified",
    }
)


def is_phantom_staff_result(result_line: str) -> bool:
    """True when a resume/nudge thread emitted a context-free STAFF_RESULT (#2773)."""
    if not result_line or "STAFF_RESULT:" not in result_line:
        return False
    _, _, rest = result_line.partition("STAFF_RESULT:")
    summary = rest.strip().splitlines()[0].strip().lower().rstrip(".")
    return summary in _PHANTOM_NUDGE_SUMMARIES


def classify_execution_result(
    *,
    cancelled: bool,
    rc: int,
    result_line: str,
    provider: str,
    transcript_path: Path | None,
    watchdog_failure_class: str = "",
    watchdog_error: str = "",
    machine: str = "",
    has_thread: bool = False,
    run: RunRecord | None = None,
    opens_pr: bool = False,
    probe: PrProbe | None = None,
) -> tuple[str, str, bool, str, str]:
    """Determine (status, failure_class, retryable, remediation, error) for a finished run."""
    if cancelled:
        return "cancelled", "", False, "", ""

    if is_phantom_staff_result(result_line):
        result_line = ""

    output_text = ""
    if transcript_path and transcript_path.exists():
        try:
            output_text = transcript_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if rc == 0 and result_line:
        fatal = classify_run_failure(
            provider=provider,
            exit_code=1,
            output_text=output_text,
            machine=machine,
        )
        if fatal.failure_class == "environment_error":
            return "failed", fatal.failure_class, fatal.retryable, fatal.remediation, fatal.error
        return "succeeded", "", False, "", ""

    diag = classify_run_failure(
        provider=provider,
        exit_code=rc,
        output_text=output_text,
        error_message=watchdog_error,
        watchdog_failure_class=watchdog_failure_class,
        watchdog_error=watchdog_error,
        result_line=result_line,
        machine=machine,
    )
    err = diag.error or (watchdog_error if watchdog_failure_class else "")
    if rc == 0 and not result_line and not err:
        err = "An unattended agent that stops to ask a question exits 0 without finishing."
    if (
        rc == 0
        and diag.failure_class == "no_result"
        and run is not None
        and opens_pr
        and probe is not None
        and remote_deliverable_present(run, probe)
    ):
        return "succeeded", "", False, "", ""
    if diag.failure_class == "needs_input":
        status = "needs_input" if has_thread else "failed"
    else:
        status = "failed"
    return status, diag.failure_class, diag.retryable, diag.remediation, err
