"""Parity checks ensuring systemd .service templates are the single source of
truth for hardening directives.

Issue #437: deploy/setup.sh used to generate runner-dashboard.service via a
heredoc that drifted from deploy/runner-dashboard.service. Same drift risk
existed between deploy/runner-autoscaler.service and
deploy/install-autoscaler.sh. These tests prevent regression by:

1. Asserting installer scripts substitute the template (sed/envsubst) rather
   than re-defining the unit inline.
2. Asserting installer scripts do not redeclare any hardening directive that
   lives in the canonical template.

The tests are pure file-parsing — no systemd or root required.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_DEPLOY = _ROOT / "deploy"

# Canonical hardening directives. Drift here is exactly what #437 prevents.
_HARDENING_DIRECTIVES = (
    "NoNewPrivileges",
    "ProtectSystem",
    "ProtectHome",
    "PrivateTmp",
    "PrivateDevices",
    "ProtectKernelTunables",
    "ProtectKernelModules",
    "ProtectControlGroups",
    "RestrictSUIDSGID",
    "RemoveIPC",
    "ReadWritePaths",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _directive_names(unit_text: str) -> set[str]:
    """Extract directive names (left of '=') from a systemd unit body, ignoring
    comments and section headers."""
    names: set[str] = set()
    for raw in unit_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            names.add(key)
    return names


def _hardening_directives_present(unit_text: str) -> set[str]:
    return _directive_names(unit_text) & set(_HARDENING_DIRECTIVES)


def _installer_redeclares_directive(installer_text: str, directive: str) -> bool:
    """Return True if the installer script contains a line that looks like a
    systemd directive assignment for ``directive`` (e.g. ``NoNewPrivileges=``)
    at the start of a line, excluding shell comments. This catches heredoc
    drift even when the heredoc body is no longer present (defence in depth).
    """
    pattern = re.compile(rf"^[ \t]*{re.escape(directive)}=", re.MULTILINE)
    return bool(pattern.search(installer_text))


# ── Dashboard pair ────────────────────────────────────────────────────────────


def test_dashboard_template_declares_full_hardening_set() -> None:
    template = _read(_DEPLOY / "runner-dashboard.service")
    present = _hardening_directives_present(template)
    missing = set(_HARDENING_DIRECTIVES) - present
    assert not missing, f"runner-dashboard.service template missing hardening directives: {sorted(missing)}"


def test_dashboard_setup_substitutes_template() -> None:
    setup = _read(_DEPLOY / "setup.sh")
    # Must reference the template path and run a substitution tool on it.
    assert "deploy/runner-dashboard.service" in setup or "runner-dashboard.service" in setup
    assert re.search(r"\b(sed|envsubst)\b", setup), "setup.sh must substitute the template via sed or envsubst"
    # The installer must invoke `install` (or equivalent) to place the rendered
    # file at /etc/systemd/system/.
    assert "/etc/systemd/system/runner-dashboard.service" in setup


def test_dashboard_setup_does_not_redeclare_hardening() -> None:
    setup = _read(_DEPLOY / "setup.sh")
    drifted = [d for d in _HARDENING_DIRECTIVES if _installer_redeclares_directive(setup, d)]
    assert not drifted, (
        f"deploy/setup.sh redeclares hardening directives that should live only in the template (issue #437): {drifted}"
    )


def test_dashboard_setup_has_no_service_heredoc() -> None:
    """Defence-in-depth: there should be no [Service] section embedded in setup.sh."""
    setup = _read(_DEPLOY / "setup.sh")
    # A literal `[Service]` line outside of a comment is the smoking gun.
    bare_service = re.search(r"^\[Service\]\s*$", setup, re.MULTILINE)
    assert bare_service is None, (
        "setup.sh contains a [Service] section; the template must be the single source of truth"
    )


# ── Autoscaler pair ───────────────────────────────────────────────────────────


def test_autoscaler_template_declares_full_hardening_set() -> None:
    template_path = _DEPLOY / "runner-autoscaler.service"
    if not template_path.exists():  # pragma: no cover - present in this repo
        return
    present = _hardening_directives_present(_read(template_path))
    missing = set(_HARDENING_DIRECTIVES) - present
    assert not missing, f"runner-autoscaler.service template missing hardening directives: {sorted(missing)}"


def test_autoscaler_installer_substitutes_template() -> None:
    installer_path = _DEPLOY / "install-autoscaler.sh"
    if not installer_path.exists():  # pragma: no cover - present in this repo
        return
    installer = _read(installer_path)
    assert "runner-autoscaler.service" in installer
    assert re.search(r"\b(sed|envsubst)\b", installer), (
        "install-autoscaler.sh must substitute the template via sed or envsubst"
    )
    assert "/etc/systemd/system/runner-autoscaler.service" in installer


def test_autoscaler_installer_does_not_redeclare_hardening() -> None:
    installer_path = _DEPLOY / "install-autoscaler.sh"
    if not installer_path.exists():  # pragma: no cover
        return
    installer = _read(installer_path)
    drifted = [d for d in _HARDENING_DIRECTIVES if _installer_redeclares_directive(installer, d)]
    assert not drifted, (
        f"install-autoscaler.sh redeclares hardening directives that should live "
        f"only in the template (issue #437): {drifted}"
    )


# ── Cross-pair parity ─────────────────────────────────────────────────────────


def test_dashboard_and_autoscaler_share_same_hardening_set() -> None:
    """The two units intentionally use the same hardening profile. If one
    drifts, the test surfaces it for explicit review."""
    autoscaler_path = _DEPLOY / "runner-autoscaler.service"
    if not autoscaler_path.exists():  # pragma: no cover
        return
    dashboard = _hardening_directives_present(_read(_DEPLOY / "runner-dashboard.service"))
    autoscaler = _hardening_directives_present(_read(autoscaler_path))
    assert dashboard == autoscaler, (
        f"Hardening directive sets diverge between dashboard and autoscaler units. "
        f"dashboard-only={sorted(dashboard - autoscaler)}, "
        f"autoscaler-only={sorted(autoscaler - dashboard)}"
    )
