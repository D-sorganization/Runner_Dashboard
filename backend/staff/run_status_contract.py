"""Design-by-contract invariants for the staff run status machine (#4269 slice 1).

Imported at module load so illegal transition tables fail fast in CI.
"""

from __future__ import annotations

from staff.run_status import LEGAL_TRANSITIONS, RUN_STATUSES, TERMINAL_STATUSES, RunStatus


class RunStatusContractError(RuntimeError):
    """Raised when ``LEGAL_TRANSITIONS`` or enum metadata violate fleet invariants."""


def validate_run_status_contract() -> None:
    """Pre: none. Post: raises ``RunStatusContractError`` when invariants are broken."""
    enum_values = {s.value for s in RunStatus}
    if set(RUN_STATUSES) != enum_values:
        raise RunStatusContractError("RUN_STATUSES must match RunStatus StrEnum values exactly")

    for status in RunStatus:
        if status not in LEGAL_TRANSITIONS:
            raise RunStatusContractError(f"missing transition row for {status!r}")
        allowed = LEGAL_TRANSITIONS[status]
        if status in allowed:
            raise RunStatusContractError(f"{status!r} must not list itself as a transition target")
        unknown = {t for t in allowed if t not in RunStatus}
        if unknown:
            raise RunStatusContractError(f"{status!r} allows unknown targets: {unknown}")

    for terminal in TERMINAL_STATUSES:
        if LEGAL_TRANSITIONS[terminal] != frozenset():
            raise RunStatusContractError(f"terminal {terminal!r} must have no outbound transitions")

    for status in RunStatus:
        if status in TERMINAL_STATUSES:
            continue
        if not LEGAL_TRANSITIONS[status]:
            raise RunStatusContractError(f"non-terminal {status!r} must allow at least one transition")

    inbound_terminals = {dst for targets in LEGAL_TRANSITIONS.values() for dst in targets if dst in TERMINAL_STATUSES}
    if inbound_terminals != set(TERMINAL_STATUSES):
        missing = set(TERMINAL_STATUSES) - inbound_terminals
        extra = inbound_terminals - set(TERMINAL_STATUSES)
        raise RunStatusContractError(f"terminal inbound mismatch: missing={missing!r} extra={extra!r}")


validate_run_status_contract()
