"""Structural regression guards for the 2026-05-18 deploy-hardening series.

These tests pin the load-bearing properties of the deploy scripts that
shipped in PRs #660, #661, #663, #664, #666, #667, #668, #669, #670.
Each test names the exact string / pattern that, if removed, would
silently reintroduce one of the bugs we just fixed.

Style note: structural greps over the script source — no script execution.
This keeps the tests portable (Linux CI, Windows dev) and fast.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_DEPLOY = _REPO / "deploy"
_BACKEND = _REPO / "backend"
_RUNBOOKS = _REPO / "docs" / "runbooks"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ─── PR #660: cleanup strict-mode regression ─────────────────────────────────


def test_cleanup_tolerates_per_runner_stop_failure() -> None:
    """runner-cleanup.sh must capture systemctl stop's exit code so a
    failed stop on runner-1 does NOT abort the loop on runners 2..N.

    The previous code ran `run systemctl stop "$unit"` bare under
    `set -Eeuo pipefail` — exit 1 from systemctl on a racy "Job for ...
    canceled" terminated the entire pass and silently skipped every
    subsequent runner. Pin the capture pattern so the regression can't
    re-land.
    """
    src = _read(_DEPLOY / "runner-cleanup.sh")
    assert "stop_rc=$?" in src, "missing stop-rc capture introduced in #660"
    # And the per-runner block must `continue` rather than exit
    assert "will retry next pass" in src, "missing continue-on-stop-fail message"


# ─── PR #661: heal-host.sh operator break-glass ──────────────────────────────


def test_heal_host_has_three_phases() -> None:
    """heal-host.sh must drain → cleanup → restart in that order.

    Skipping cleanup or reordering would defeat the whole purpose
    (cleanup needs units stopped to GC their `_work/_temp` residue).
    """
    src = _read(_DEPLOY / "heal-host.sh")
    drain = src.find("=== drain ===")
    cleanup = src.find("=== cleanup ===")
    restart = src.find("=== restart ===")
    assert drain >= 0 and cleanup >= 0 and restart >= 0, "missing phase markers"
    assert drain < cleanup < restart, "phases must run in order: drain → cleanup → restart"


def test_heal_host_force_kills_orphan_workers() -> None:
    """The post-drain sweep must SIGKILL leftover Runner.Worker
    processes. Without this, KillMode=process leaves orphans that block
    the cleanup pass."""
    src = _read(_DEPLOY / "heal-host.sh")
    assert "pkill -KILL -f 'Runner\\.Worker spawnclient'" in src, "missing post-drain orphan-Worker kill"


# ─── PR #664 + #666: runner unit drop-ins (race + orphan fix) ───────────────


def test_migrate_runner_units_sets_kill_mode_mixed() -> None:
    """The drop-in must change KillMode from `process` (default) to
    `mixed` so the cgroup-wide signal cascade reaches Worker children.
    Without this, `KillMode=process` leaves Workers orphaned on stop.
    """
    src = _read(_DEPLOY / "migrate-runner-units.sh")
    assert "KillMode=mixed" in src, "drop-in must set KillMode=mixed"


def test_update_deployed_ensures_runner_hardening() -> None:
    """Routine deploys must re-apply the runner-unit hardening so a host
    can never silently run KillMode=process.

    install-runner-maintenance.sh applies the KillMode=mixed drop-ins, but
    hosts set up before it existed (or where it never ran) keep
    KillMode=process — which orphans Runner.Worker children and abruptly
    kills in-flight jobs on stop. update-deployed.sh must idempotently
    ensure the drop-ins exist on every deploy, without restarting busy
    units (the drop-in takes effect on the unit's next natural restart).
    Observed 2026-05-29 on DeskComputer: its runner units lacked the
    drop-in entirely because no deploy step ever ensured it.
    """
    src = _read(_DEPLOY / "update-deployed.sh")
    assert "ensure_runner_hardening" in src, "deploy must call ensure_runner_hardening"
    assert "migrate-runner-units.sh" in src, "hardening must be applied via migrate-runner-units.sh"
    assert "10-runner-dashboard-busy-lock.conf" in src, "must check for the drop-in file"
    assert "KillMode=mixed" in src, "must verify KillMode=mixed presence"
    # Must NOT restart units (busy runners are running jobs).
    assert "--restart-units" not in src.split("ensure_runner_hardening()")[1].split("\n}")[0], (
        "ensure_runner_hardening must not pass --restart-units (would kill in-flight jobs)"
    )


def test_migrate_runner_units_passes_unit_name_via_specifier() -> None:
    """The ExecStop= drop-in must pass %n (full unit name) to
    force-drain.sh as $1. systemd does NOT export $SYSTEMD_UNIT to
    ExecStop= portably; without %n, force-drain.sh can't resolve the
    correct WorkingDirectory and silently no-ops.
    """
    src = _read(_DEPLOY / "migrate-runner-units.sh")
    assert "ExecStop=-${HOOK_DIR}/force-drain.sh %n" in src, "ExecStop= must pass %n to force-drain"


def test_migrate_runner_units_default_hook_dir_matches_installer() -> None:
    """The drop-in's default HOOK_DIR must match where
    install-runner-maintenance.sh puts the hooks (/usr/local/bin/runner-hooks).
    The old default (/opt/runner-dashboard/deploy/runner-hooks) didn't
    exist on any host in the fleet.
    """
    src = _read(_DEPLOY / "migrate-runner-units.sh")
    assert 'HOOK_DIR="${HOOK_DIR:-/usr/local/bin/runner-hooks}"' in src


def test_force_drain_reads_unit_name_from_arg1() -> None:
    """force-drain.sh must read the unit name from $1 (passed via %n)
    first, with $SYSTEMD_UNIT as a fallback. Pre-fix it only read
    $SYSTEMD_UNIT, which systemd doesn't reliably set."""
    src = _read(_DEPLOY / "runner-hooks" / "force-drain.sh")
    assert 'UNIT="${1:-${SYSTEMD_UNIT:-}}"' in src


def test_force_drain_resolves_workdir_via_agentname() -> None:
    """force-drain.sh's filesystem fallback must look up the runner's
    .runner config and match on agentName, because runner-dir names
    (runner-N) don't match the system runner names (d-sorg-local-Desktop-N).
    """
    src = _read(_DEPLOY / "runner-hooks" / "force-drain.sh")
    assert "agentName" in src, "fallback must consult .runner agentName"


def test_force_drain_never_blocks_systemd_stop() -> None:
    """The script must exit 0 even when nothing was found — otherwise an
    ExecStop= helper that returns non-zero would prevent systemd from
    marking the unit inactive."""
    src = _read(_DEPLOY / "runner-hooks" / "force-drain.sh")
    assert "exit 0" in src


def test_job_started_hook_writes_metadata_lockfile() -> None:
    """The JOB_STARTED hook must write a lockfile under
    $RUNNER_BUSY_LOCK_DIR with the runner's pid + workflow context so
    autoscaler/cleanup can use it as a busy signal."""
    src = _read(_DEPLOY / "runner-hooks" / "job-started.sh")
    for key in ["pid=", "runner=", "job=", "workflow=", "run_id="]:
        assert key in src, f"job-started lockfile must include {key}"


def test_job_started_hook_keeps_worktree_lock_scan_bounded_and_opt_in() -> None:
    """Per-job hooks must not scan every runner worktree by default."""
    src = _read(_DEPLOY / "runner-hooks" / "job-started.sh")
    assert "RUNNER_HOOK_ENABLE_WORKTREE_LOCK_CLEANUP:-0" in src
    assert "RUNNER_HOOK_LOCK_CLEANUP_TIMEOUT_SECONDS:-10" in src
    assert 'timeout "${RUNNER_HOOK_LOCK_CLEANUP_TIMEOUT_SECONDS:-10}s" find' in src


def test_job_completed_hook_removes_the_lockfile() -> None:
    src = _read(_DEPLOY / "runner-hooks" / "job-completed.sh")
    assert "rm -f " in src and "$LOCK_FILE" in src


# ─── PR #667: deploy-host.sh single-command entrypoint ───────────────────────


def test_deploy_host_sets_uv_link_mode_copy() -> None:
    """When the source repo lives on /mnt/c (Windows-mounted), uv's
    default hardlink install fails with cross-filesystem ENOENT. The
    script must default UV_LINK_MODE=copy upfront."""
    src = _read(_DEPLOY / "deploy-host.sh")
    assert 'UV_LINK_MODE="${UV_LINK_MODE:-copy}"' in src


def test_deploy_host_invokes_deploy_check() -> None:
    """The whole point of deploy-check.sh is to fail loudly when a
    deploy succeeds but the dashboard is silently misconfigured. The
    one-command flow must run it at the end."""
    src = _read(_DEPLOY / "deploy-host.sh")
    assert "deploy-check.sh" in src


def test_deploy_host_dry_run_skips_sudo_writes() -> None:
    """--dry-run must NOT invoke install-runner-maintenance.sh (which
    has sudo writes). The script must explicitly skip it."""
    src = _read(_DEPLOY / "deploy-host.sh")
    assert "dry-run: skipping install-runner-maintenance.sh" in src


# ─── PR #668: machine registry path + FLEET_NODES auto-derive ────────────────


def test_machine_registry_allows_module_dir() -> None:
    """load_machine_registry must include `Path(__file__).parent` in
    its explicit allowed_roots, so the YAML shipping next to the .py
    can be loaded on deployed installs (which aren't git checkouts)."""
    src = _read(_BACKEND / "machine_registry.py")
    assert "Path(__file__).resolve().parent" in src
    assert "allowed_roots=explicit_roots" in src


def test_server_auto_derives_fleet_nodes_from_registry() -> None:
    """When FLEET_NODES env is empty, server.py must derive it from the
    registry. Without this, peer federation never happens unless the
    operator hand-maintains the env var (which nobody did)."""
    src = _read(_BACKEND / "server.py")
    assert "AUTODERIVE_FLEET_NODES" in src
    assert "FLEET_NODES_SOURCE" in src
    assert "FLEET_NODES auto-derived from registry" in src


def test_diagnostics_endpoint_exists() -> None:
    """The /api/diagnostics endpoint must be declared and always return
    200 (never raise) so it's a reliable deploy-health surface."""
    src = _read(_BACKEND / "server.py")
    assert '@app.get("/api/diagnostics")' in src
    assert "def _diagnostics_payload" in src


def test_deploy_check_parses_diagnostics_via_tempfile() -> None:
    """The earlier inline `printf | python3 -c` pattern fought bash
    heredoc semantics. The fix uses a tempfile so the JSON parse is
    robust against any future curl response payload."""
    src = _read(_DEPLOY / "deploy-check.sh")
    assert "mktemp /tmp/diag" in src or "mktemp /tmp/diag.XXXXXX.json" in src


def test_deploy_check_warns_on_autoscaler_driven_scaledown() -> None:
    """When the autoscaler is healthy and has legitimately scaled idle
    units down, deploy-check must report WARN not FAIL. Otherwise every
    deploy under load would falsely fail."""
    src = _read(_DEPLOY / "deploy-check.sh")
    assert "autoscaler-driven scale-down likely" in src


# ─── PR #668 follow-up: world-writable YAML normalization ────────────────────


def test_update_deployed_chmods_yaml_for_security_validator() -> None:
    """Files copied from /mnt/c come over with mode 0777 because NTFS
    can't represent POSIX bits. security.py rejects world-writable
    config; the deploy must normalize *.yml/*.yaml/*.json to 0644."""
    src = _read(_DEPLOY / "update-deployed.sh")
    assert "chmod 0644" in src
    assert "'*.yml'" in src and "'*.yaml'" in src and "'*.json'" in src


def test_artifact_installer_chmods_yaml_for_security_validator() -> None:
    """Artifact installs must also normalize copied config modes.

    Otherwise a release artifact deployed from a Windows-mounted checkout can
    pass CI and still break fleet federation at runtime.
    """
    src = _read(_DEPLOY / "install-dashboard-artifact.sh")
    assert "chmod 0644" in src
    assert "'*.yml'" in src and "'*.yaml'" in src and "'*.json'" in src


# ─── Issues #688/#691: stale queue cleanup stays preview/capped ─────────────


def test_scheduled_maintenance_prefers_stale_api_preview_with_caps() -> None:
    """Maintenance must not silently fire an uncapped stale purge."""
    src = _read(_DEPLOY / "scheduled-dashboard-maintenance.sh")
    assert "STALE_QUEUE_DRY_RUN:-1" in src
    assert "STALE_QUEUE_MAX_CANCEL:-10" in src
    assert "STALE_QUEUE_REASON_FILTER" in src
    assert "/api/queue/stale?min_age_minutes=" in src
    assert '\\"dry_run\\": true' in src
    assert "refusing uncapped purge" in src


def test_queue_stuck_runbook_documents_safe_stale_policy() -> None:
    """Runbook examples must match the stale API/reaper safety controls."""
    src = _read(_RUNBOOKS / "queue-stuck.md")
    for token in [
        "/api/queue/status",
        "/api/queue/stale?min_age_minutes=30",
        "/api/queue/purge-stale",
        '"dry_run": true',
        "unsatisfiable_runner_labels",
        "superseded_pr_head",
        "safe_to_cancel=true",
        "max-cancel=5",
        "STALE_QUEUE_DRY_RUN=0",
        "STALE_QUEUE_MAX_CANCEL=5",
    ]:
        assert token in src


# ─── PR #668 follow-up: Tailscale CGNAT + .ts.net allowance ──────────────────


def test_validate_fleet_node_url_allows_cgnat_and_ts_net() -> None:
    """Tailscale assigns 100.64.0.0/10 CGNAT addresses; ipaddress's
    is_private excludes that range. Without explicit handling, every
    Tailscale-routed peer gets rejected and federation never works.
    Same for *.ts.net MagicDNS hostnames."""
    src = _read(_BACKEND / "security.py")
    assert "100.64.0.0/10" in src
    assert ".ts.net" in src


# ─── PR #669: deploy-host frontend build step ────────────────────────────────


def test_update_deployed_builds_frontend_and_syncs_dist() -> None:
    """server.py serves from <dashboard>/dist/. Without `npm run build`
    + sync of dist/, the favicon, icons, manifest, and the entire SPA
    bundle 404 (because dist/ is empty on a fresh install)."""
    src = _read(_DEPLOY / "update-deployed.sh")
    assert "npm install --no-audit --no-fund --package-lock=false" in src
    assert "npm run build" in src
    assert 'sync_dir "$REPO/dist" "$DEPLOY_DIR/dist"' in src


# ─── PR #669: health-check fail-fast ─────────────────────────────────────────


def test_health_uses_named_github_timeout() -> None:
    """Health check must use a NAMED timeout constant (HEALTH_GH_API_S)
    so an operator changing the budget doesn't have to grep multiple
    call sites. The original 1 s value was too tight (gh api subprocess
    startup takes 5-8 s on a typical host); the constant now lives at
    10 s but the named-constant contract is what matters here."""
    src = _read(_BACKEND / "dashboard_config" / "timeouts.py")
    assert "HEALTH_GH_API_S" in src


def test_health_gh_api_timeout_is_realistic() -> None:
    """The original 1 s value killed every health check because `gh api`
    forks a Go binary, loads its config, signs the request, and only
    then makes the network call — that takes 5-8 s on a typical host
    even when curl-direct against api.github.com takes 130 ms. Pin a
    floor so a future operator can't re-tighten back to a value that
    breaks the dashboard's GitHub view.

    See d-sorg-local-ControlTower 2026-05-18: the 1 s budget made the
    Overview tab render 0 % success rate because /api/stats depends on
    the same `gh api` path and times out identically."""
    src = _read(_BACKEND / "dashboard_config" / "timeouts.py")
    import re

    match = re.search(r"HEALTH_GH_API_S:\s*int\s*=\s*(\d+)", src)
    assert match is not None, "HEALTH_GH_API_S declaration not found"
    value = int(match.group(1))
    assert value >= 5, (
        f"HEALTH_GH_API_S={value}s is below the 5s floor; `gh api` subprocess "
        f"startup alone takes 5-8s. Anything lower kills every health check."
    )


def test_metrics_imports_psutil_directly_not_through_server() -> None:
    """metrics.py must not depend on server.py for anything.

    Originally this guarded against pulling psutil through a `from server import`
    block. As of issue #940 the duplicate /api/system and /api/fleet/status
    handlers (the only code that referenced server internals) were removed, so
    metrics.py must have no server import at all.
    """
    src = _read(_BACKEND / "metrics.py")
    assert "from server import" not in src, "metrics.py must not import from server (#940)"
    assert "import server" not in src, "metrics.py must not import server (#940)"
    # The bad pattern was "from server import (... psutil, ...)" — make
    # sure psutil never appears in such a multi-line import block.
    assert "psutil," not in src


# ─── PR #670: static-serve routes for /favicon, /icons, /sw.js, etc. ─────────


def test_server_mounts_icons_dir() -> None:
    """/icons/<name>.png must be mounted as StaticFiles so PNGs are
    served with image/png Content-Type instead of being shadowed by the
    SPA catch-all (which would return index.html as text/html)."""
    src = _read(_BACKEND / "server.py")
    assert '"/icons"' in src and "StaticFiles(directory=str(_icons_dir))" in src


def test_server_has_static_root_routes() -> None:
    """Windows taskbar shortcuts probe /favicon.ico; PWA install
    requires /sw.js at origin-root; /offline.html and /robots.txt are
    pre-existing static assets that were also shadowed."""
    src = _read(_BACKEND / "server.py")
    for path in [
        '@app.get("/favicon.ico")',
        '@app.get("/sw.js")',
        '@app.get("/offline.html")',
        '@app.get("/robots.txt")',
    ]:
        assert path in src, f"missing route: {path}"
