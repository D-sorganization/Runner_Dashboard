"""Static contract tests for ``deploy/decouple-docker-boot.sh``.

The script is Linux/WSL-only at runtime (it drives ``systemctl``), so we assert
on its text rather than executing it. The contract it must preserve:

* docker.service and containerd.service are removed from the boot transaction
  (``systemctl disable``) so they no longer gate WSL's ~10s WaitForBootProcess
  window;
* docker.socket stays enabled so on-demand activation still works;
* the post-boot timer (canonical copy in ``deploy/docker-delayed-start.timer``)
  is installed and enabled;
* the script is idempotent and supports ``--dry-run``;
* it never *stops* docker/containerd (that would interrupt in-flight jobs).
"""

from __future__ import annotations

from pathlib import Path

DEPLOY_DIR = Path(__file__).resolve().parents[2] / "deploy"
SCRIPT = DEPLOY_DIR / "decouple-docker-boot.sh"


def _text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_script_exists_and_is_bash() -> None:
    assert SCRIPT.is_file(), f"missing script: {SCRIPT}"
    assert _text().splitlines()[0].startswith("#!"), "missing shebang"


def test_disables_docker_and_containerd_from_boot() -> None:
    text = _text()
    assert "docker.service" in text and "containerd.service" in text
    assert "systemctl disable" in text, "must disable units from the boot transaction"


def test_keeps_docker_socket_enabled() -> None:
    """On-demand activation must survive so nothing needs Docker-at-boot."""
    assert "docker.socket" in _text()


def test_installs_and_enables_the_post_boot_timer() -> None:
    text = _text()
    assert "docker-delayed-start.timer" in text
    assert "systemctl enable docker-delayed-start.timer" in text
    assert "systemctl daemon-reload" in text


def test_never_stops_running_docker() -> None:
    """`disable` only removes the boot symlink; stopping would kill live jobs."""
    text = _text()
    assert "systemctl stop docker" not in text
    assert "systemctl stop containerd" not in text


def test_supports_dry_run_and_requires_root() -> None:
    text = _text()
    assert "--dry-run" in text
    assert "EUID" in text and "must run as root" in text


def test_is_idempotent_guarded() -> None:
    """Re-running must be safe: guarded by is-enabled checks."""
    assert "is-enabled" in _text()
