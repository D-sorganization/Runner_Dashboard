#!/usr/bin/env bash
# Root-only primitives for the OGLaptop qualified-release transaction.

set -euo pipefail

QUALIFIED_STATE_ROOT="/var/lib/runner-dashboard-qualified-deploy"
QUALIFIED_INBOX_ROOT="${QUALIFIED_STATE_ROOT}/inbox"
QUALIFIED_TRANSACTION_ROOT="${QUALIFIED_STATE_ROOT}/transactions"
QUALIFIED_RUNNER_USER="dieterolson"
QUALIFIED_RUNNER_HOME="/home/${QUALIFIED_RUNNER_USER}"
QUALIFIED_DEPLOY_DIR="${QUALIFIED_RUNNER_HOME}/actions-runners/dashboard"
QUALIFIED_RUNNER_ROOT="${QUALIFIED_RUNNER_HOME}/actions-runners"
QUALIFIED_CONFIG_DIR="${QUALIFIED_RUNNER_HOME}/.config/runner-dashboard"
QUALIFIED_SHARE_DIR="${QUALIFIED_RUNNER_HOME}/.local/share/runner-dashboard"
QUALIFIED_SCHEDULE="${QUALIFIED_CONFIG_DIR}/runner-schedule.json"
QUALIFIED_SCHEDULE_SOURCE="/usr/local/share/runner-dashboard/runner-schedule-oglaptop.json"
QUALIFIED_RELEASE_ROOT="/opt/runner-dashboard-qualified/releases"
QUALIFIED_EVIDENCE_NAME="redacted-evidence.json"
COSIGN_BIN="/usr/local/bin/cosign"

fail() {
    printf '[FAIL] %s\n' "$*" >&2
    return 1
}

journal_event() {
    local event="$1"
    local result="${2:-ok}"
    /usr/bin/python3 - "${TRANSACTION_JOURNAL}" "${event}" "${result}" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

path = Path(sys.argv[1])
record = {
    "timestamp": datetime.now(UTC).isoformat(),
    "event": sys.argv[2],
    "result": sys.argv[3],
}
with path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
PY
}

copy_inbox_asset() {
    local source="$1"
    local destination="$2"
    [[ -f "${source}" && ! -L "${source}" ]] || fail "release asset is not a regular file"
    [[ "$(stat -c '%U' "${source}")" == "${QUALIFIED_RUNNER_USER}" ]] \
        || fail "release asset has an unexpected owner"
    local mode
    mode="$(stat -c '%a' "${source}")"
    (( (8#${mode} & 8#022) == 0 )) || fail "release asset is group/other writable"
    install -o root -g root -m 0600 "${source}" "${destination}"
}

verify_sha256() {
    local artifact="$1"
    local expected="$2"
    local actual
    actual="$(sha256sum "${artifact}" | awk '{print $1}')"
    [[ "${actual}" == "${expected}" ]] || fail "artifact checksum mismatch"
}

verify_cosign_bundle() {
    local artifact="$1"
    local bundle="$2"
    "${COSIGN_BIN}" verify-blob \
        --bundle "${bundle}" \
        --certificate-identity-regexp \
        '^https://github.com/D-sorganization/Runner_Dashboard/.github/workflows/release.yml@refs/heads/main$' \
        --certificate-oidc-issuer 'https://token.actions.githubusercontent.com' \
        "${artifact}" > /dev/null
}

validate_worker_ancestry() {
    local expected_pid="$1"
    local cursor="${PPID}"
    local command_line
    local expected_repository="D-sorg"
    expected_repository+="anization/Runner_Dashboard"
    local workflow_proven=0
    [[ -r "/proc/${expected_pid}/cmdline" ]] || fail "workflow worker process is absent"
    command_line="$(tr '\0' ' ' < "/proc/${expected_pid}/cmdline")"
    [[ "${command_line}" =~ /runner-1/bin(\.[^/[:space:]]+)?/Runner\.Worker([[:space:]]|$) ]] \
        || fail "workflow worker is not runner-1"
    while [[ "${cursor}" =~ ^[1-9][0-9]*$ && "${cursor}" -gt 1 ]]; do
        if [[ -r "/proc/${cursor}/environ" ]]; then
            local environment
            environment="$(tr '\0' '\n' < "/proc/${cursor}/environ")"
            if grep -Fxq "GITHUB_REPOSITORY=${expected_repository}" <<< "${environment}" \
                && grep -Fxq "GITHUB_WORKFLOW_REF=${expected_repository}/.github/workflows/deploy-qualified-release.yml@refs/heads/main" <<< "${environment}" \
                && grep -Fxq "GITHUB_RUN_ID=${GITHUB_RUN_ID}" <<< "${environment}" \
                && grep -Fxq "GITHUB_RUN_ATTEMPT=${GITHUB_RUN_ATTEMPT}" <<< "${environment}" \
                && grep -Fxq 'RUNNER_NAME=d-sorg-local-Oglaptop-1' <<< "${environment}"; then
                workflow_proven=1
            fi
        fi
        if [[ "${cursor}" == "${expected_pid}" ]]; then
            [[ "${workflow_proven}" -eq 1 ]] \
                || fail "privileged caller is not the protected-main deployment workflow"
            return 0
        fi
        cursor="$(awk '/^PPid:/ {print $2}' "/proc/${cursor}/status" 2>/dev/null || true)"
    done
    fail "workflow worker is not an ancestor of the privileged transaction"
}

validate_archive() {
    local artifact="$1"
    /usr/bin/python3 - "${artifact}" <<'PY'
import sys
import tarfile
from pathlib import PurePosixPath

with tarfile.open(sys.argv[1], "r:gz") as archive:
    members = archive.getmembers()
    if not members or len(members) > 10000:
        raise SystemExit("archive member count is outside the qualified bound")
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not member.name.strip():
            raise SystemExit("archive contains an unsafe path")
        if member.issym() or member.islnk() or member.isdev() or member.isfifo():
            raise SystemExit("archive contains a link or special file")
PY
}

validate_artifact_metadata() {
    local stage="$1"
    local expected_version="$2"
    local expected_commit="$3"
    /usr/bin/python3 - "${stage}" "${expected_version}" "${expected_commit}" <<'PY'
import json
import sys
from pathlib import Path

stage = Path(sys.argv[1])
required = (
    "VERSION", "deployment.json", "FILES.txt", "requirements.lock.txt",
    "backend/wheels", "deploy/install-dashboard-artifact.sh",
    "deploy/runner-scheduler.py",
)
for relative in required:
    if not (stage / relative).exists():
        raise SystemExit(f"artifact missing required path: {relative}")
metadata = json.loads((stage / "deployment.json").read_text(encoding="utf-8"))
compatibility = metadata.get("compatibility")
if not isinstance(compatibility, dict):
    raise SystemExit("artifact compatibility block is absent")
expected = {
    "version": sys.argv[2],
    "git_sha": sys.argv[3],
}
for field, value in expected.items():
    if metadata.get(field) != value:
        raise SystemExit(f"artifact {field} does not match the request")
if (stage / "VERSION").read_text(encoding="utf-8").strip() != sys.argv[2]:
    raise SystemExit("artifact VERSION does not match the request")
if compatibility.get("artifact_schema") != "runner-dashboard-artifact-v2":
    raise SystemExit("artifact schema is not runner-dashboard-artifact-v2")
if compatibility.get("python_minor") != "3.12":
    raise SystemExit("qualified OGLaptop deployment requires Python 3.12")
if compatibility.get("service_name") != "runner-dashboard.service":
    raise SystemExit("artifact service identity is not runner-dashboard.service")
PY
}

create_mutable_manifest() {
    local manifest="$1"
    local payload="$2"
    /usr/bin/python3 - "${manifest}" "${payload}" "${QUALIFIED_DEPLOY_DIR}" \
        "${QUALIFIED_CONFIG_DIR}" "${QUALIFIED_SHARE_DIR}" "${QUALIFIED_SCHEDULE}" <<'PY'
import hashlib
import json
import os
import stat
import sys
from pathlib import Path

manifest, payload = Path(sys.argv[1]), Path(sys.argv[2])
roots = [Path(value) for value in sys.argv[3:6]]
schedule = Path(sys.argv[6])
patterns = (".env", ".*_state.json", "*_history.json", "*.db", "*.db-*", "*.sqlite", "*.sqlite3", "*.sqlite-*", "*.jsonl")
payload.mkdir(parents=True, exist_ok=False)
records = []
seen = set()
for root in roots:
    if not root.exists():
        continue
    candidates = []
    if root == roots[0]:
        for pattern in patterns:
            candidates.extend(root.rglob(pattern))
    else:
        candidates.extend(path for path in root.rglob("*") if path.is_file())
    for path in sorted(set(candidates)):
        if path == schedule or not path.is_file() or path.is_symlink() or path in seen:
            continue
        seen.add(path)
        data = path.read_bytes()
        destination = payload / str(len(records))
        destination.write_bytes(data)
        metadata = path.stat()
        records.append({
            "path": str(path),
            "payload": destination.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "mode": stat.S_IMODE(metadata.st_mode),
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
        })
manifest.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

restore_mutable_manifest() {
    local manifest="$1"
    local payload="$2"
    /usr/bin/python3 - "${manifest}" "${payload}" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

manifest, payload = Path(sys.argv[1]), Path(sys.argv[2])
records = json.loads(manifest.read_text(encoding="utf-8"))
for record in records:
    source = payload / record["payload"]
    if hashlib.sha256(source.read_bytes()).hexdigest() != record["sha256"]:
        raise SystemExit("mutable-state payload failed its checksum")
    destination = Path(record["path"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".qualified-deploy.tmp")
    temporary.write_bytes(source.read_bytes())
    os.chmod(temporary, record["mode"])
    os.chown(temporary, record["uid"], record["gid"])
    temporary.replace(destination)
PY
}

verify_mutable_manifest() {
    local manifest="$1"
    /usr/bin/python3 - "${manifest}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

for record in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")):
    path = Path(record["path"])
    if not path.is_file() or path.is_symlink():
        raise SystemExit("mutable-state file disappeared")
    if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
        raise SystemExit("mutable-state file changed during installation")
PY
}

snapshot_path() {
    local source="$1"
    local destination="$2"
    if [[ -e "${source}" ]]; then
        cp -a -- "${source}" "${destination}"
    else
        : > "${destination}.absent"
    fi
}

record_rollback_baseline() {
    install -d -o root -g root -m 0700 "${ROLLBACK_SNAPSHOT}"
    install -d -o root -g root -m 0700 "${ROLLBACK_SNAPSHOT}/systemd-before"
    for path in \
        /etc/systemd/system/runner-dashboard.service.d/30-qualified-capacity.conf \
        /etc/systemd/system/runner-scheduler.service \
        /etc/systemd/system/runner-scheduler.timer; do
        snapshot_path "${path}" "${ROLLBACK_SNAPSHOT}/systemd-before/$(basename "${path}")"
    done
    snapshot_path "${QUALIFIED_SCHEDULER_RELEASE}" "${ROLLBACK_SNAPSHOT}/scheduler-release"
    systemctl is-active runner-dashboard.service > "${ROLLBACK_SNAPSHOT}/dashboard.active" || true
    systemctl is-active runner-scheduler.service > "${ROLLBACK_SNAPSHOT}/scheduler-service.active" || true
    systemctl is-active runner-scheduler.timer > "${ROLLBACK_SNAPSHOT}/scheduler.active" || true
    systemctl is-enabled runner-scheduler.timer > "${ROLLBACK_SNAPSHOT}/scheduler.enabled" || true
    systemctl is-active runner-autoscaler.service > "${ROLLBACK_SNAPSHOT}/autoscaler.active" || true
    systemctl is-enabled runner-autoscaler.service > "${ROLLBACK_SNAPSHOT}/autoscaler.enabled" || true
}

quiesce_for_snapshot() {
    systemctl stop runner-scheduler.timer 2>/dev/null || true
    systemctl stop runner-scheduler.service 2>/dev/null || true
    systemctl disable --now runner-autoscaler.service 2>/dev/null || true
    systemctl stop runner-dashboard.service
    systemctl is-active --quiet runner-dashboard.service && fail "dashboard did not quiesce"
    systemctl is-active --quiet runner-scheduler.service && fail "scheduler service did not quiesce"
    systemctl is-active --quiet runner-scheduler.timer && fail "scheduler timer did not quiesce"
    systemctl is-active --quiet runner-autoscaler.service && fail "autoscaler did not quiesce"
    return 0
}

verify_snapshot_tree() {
    local source="$1"
    local backup="$2"
    if [[ -d "${source}" ]]; then
        [[ -d "${backup}" ]] || fail "rollback directory snapshot is absent"
        local snapshot_diff
        snapshot_diff="$(rsync -acni --delete "${source}/" "${backup}/")"
        [[ -z "${snapshot_diff}" ]] || fail "rollback directory snapshot changed"
    else
        [[ -f "${backup}.absent" ]] || fail "rollback absence marker is missing"
    fi
}

verify_quiesced_snapshot() {
    verify_snapshot_tree "${QUALIFIED_DEPLOY_DIR}" "${ROLLBACK_SNAPSHOT}/dashboard"
    verify_snapshot_tree "${QUALIFIED_CONFIG_DIR}" "${ROLLBACK_SNAPSHOT}/config"
    verify_snapshot_tree "${QUALIFIED_SHARE_DIR}" "${ROLLBACK_SNAPSHOT}/local-share"
}

create_quiesced_rollback_snapshot() {
    snapshot_path "${QUALIFIED_DEPLOY_DIR}" "${ROLLBACK_SNAPSHOT}/dashboard"
    snapshot_path "${QUALIFIED_CONFIG_DIR}" "${ROLLBACK_SNAPSHOT}/config"
    snapshot_path "${QUALIFIED_SHARE_DIR}" "${ROLLBACK_SNAPSHOT}/local-share"
    [[ ! -f "${QUALIFIED_DEPLOY_DIR}/deployment.json" ]] \
        || cmp -s "${QUALIFIED_DEPLOY_DIR}/deployment.json" "${ROLLBACK_SNAPSHOT}/dashboard/deployment.json" \
        || fail "rollback deployment identity changed"
    verify_quiesced_snapshot
}

mark_quiesced_snapshot_complete() {
    verify_quiesced_snapshot
    install -o root -g root -m 0400 /dev/null "${SNAPSHOT_COMPLETE}"
}

restore_path() {
    local backup="$1"
    local destination="$2"
    if [[ -e "${backup}" ]]; then
        install -d "$(dirname "${destination}")"
        cp -a -- "${backup}" "${destination}.restore"
        rm -rf -- "${destination}"
        mv -- "${destination}.restore" "${destination}"
    elif [[ -f "${backup}.absent" ]]; then
        rm -rf -- "${destination}"
    fi
}

restore_rollback_snapshot() {
    systemctl stop runner-scheduler.timer runner-scheduler.service 2>/dev/null || true
    systemctl disable --now runner-autoscaler.service 2>/dev/null || true
    systemctl stop runner-dashboard.service 2>/dev/null || true
    if [[ -f "${SNAPSHOT_COMPLETE}" && ! -L "${SNAPSHOT_COMPLETE}" \
        && "$(stat -c '%U:%G' "${SNAPSHOT_COMPLETE}")" == "root:root" ]]; then
        restore_path "${ROLLBACK_SNAPSHOT}/dashboard" "${QUALIFIED_DEPLOY_DIR}"
        restore_path "${ROLLBACK_SNAPSHOT}/config" "${QUALIFIED_CONFIG_DIR}"
        restore_path "${ROLLBACK_SNAPSHOT}/local-share" "${QUALIFIED_SHARE_DIR}"
    fi
    restore_path "${ROLLBACK_SNAPSHOT}/scheduler-release" "${QUALIFIED_SCHEDULER_RELEASE}"
    restore_path "${ROLLBACK_SNAPSHOT}/systemd-before/30-qualified-capacity.conf" \
        /etc/systemd/system/runner-dashboard.service.d/30-qualified-capacity.conf
    restore_path "${ROLLBACK_SNAPSHOT}/systemd-before/runner-scheduler.service" \
        /etc/systemd/system/runner-scheduler.service
    restore_path "${ROLLBACK_SNAPSHOT}/systemd-before/runner-scheduler.timer" \
        /etc/systemd/system/runner-scheduler.timer
    systemctl daemon-reload
    if [[ "$(cat "${ROLLBACK_SNAPSHOT}/scheduler.enabled")" == "enabled" ]]; then
        systemctl enable runner-scheduler.timer
    else
        systemctl disable runner-scheduler.timer 2>/dev/null || true
    fi
    if [[ "$(cat "${ROLLBACK_SNAPSHOT}/autoscaler.enabled")" == "enabled" ]]; then
        systemctl enable runner-autoscaler.service
    else
        systemctl disable runner-autoscaler.service 2>/dev/null || true
    fi
    if [[ "$(cat "${ROLLBACK_SNAPSHOT}/dashboard.active")" == "active" ]]; then
        systemctl restart runner-dashboard.service
    else
        systemctl stop runner-dashboard.service 2>/dev/null || true
    fi
    if [[ "$(cat "${ROLLBACK_SNAPSHOT}/autoscaler.active")" == "active" ]]; then
        systemctl start runner-autoscaler.service
    else
        systemctl stop runner-autoscaler.service 2>/dev/null || true
    fi
    if [[ "$(cat "${ROLLBACK_SNAPSHOT}/scheduler-service.active")" == "active" ]]; then
        systemctl start runner-scheduler.service
    fi
    if [[ "$(cat "${ROLLBACK_SNAPSHOT}/scheduler.active")" == "active" ]]; then
        systemctl start runner-scheduler.timer
    else
        systemctl stop runner-scheduler.timer 2>/dev/null || true
    fi
}
