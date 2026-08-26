#!/usr/bin/env bash
# Root-owned, no-argument OGLaptop deployment transaction (issue #1138).
set -euo pipefail
umask 0077
# The bootstrap copies this library to a root-owned immutable authority path.
# shellcheck source=deploy/qualified-release-lib.sh
source /usr/local/lib/runner-dashboard/qualified-release-lib.sh
# shellcheck source=deploy/qualified-release-runtime-lib.sh
source /usr/local/lib/runner-dashboard/qualified-release-runtime-lib.sh
SCHEDULER_CYCLE_TIMEOUT_SECONDS=330
MUTATION_STARTED=0
TRANSACTION_COMMITTED=0
ROLLBACK_RESULT="not-required"
read_request() {
    local request_file="$1"
    mapfile -d '' REQUEST_FIELDS < <(/usr/bin/python3 - "${request_file}" <<'PY'
import json
import re
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "schema", "operation", "transaction_id", "version", "tag", "commit",
    "artifact_sha256", "worker_pid", "runner_name", "github_run_id",
    "github_run_attempt",
}
if set(payload) != expected:
    raise SystemExit("request keys do not match the closed schema")
if payload["schema"] != "qualified-release-request-v1" or payload["operation"] != "apply":
    raise SystemExit("request schema or operation is unsupported")
version = str(payload["version"])
tag = str(payload["tag"])
commit = str(payload["commit"])
checksum = str(payload["artifact_sha256"])
run_id = str(payload["github_run_id"])
attempt = str(payload["github_run_attempt"])
transaction_id = str(payload["transaction_id"])
if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
    raise SystemExit("version is not strict semver")
if tag != f"v{version}":
    raise SystemExit("tag does not match version")
if not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("commit is not a lowercase full SHA")
if not re.fullmatch(r"[0-9a-f]{64}", checksum):
    raise SystemExit("artifact checksum is not lowercase SHA-256")
if not re.fullmatch(r"[1-9][0-9]*", run_id) or not re.fullmatch(r"[1-9][0-9]*", attempt):
    raise SystemExit("workflow identity is invalid")
if transaction_id != f"run-{run_id}-attempt-{attempt}":
    raise SystemExit("transaction identity does not match workflow identity")
if payload["runner_name"] != "d-sorg-local-Oglaptop-1":
    raise SystemExit("request did not originate from the allowlisted runner")
worker_pid = str(payload["worker_pid"])
if not re.fullmatch(r"[1-9][0-9]*", worker_pid):
    raise SystemExit("worker PID is invalid")
for value in (transaction_id, version, tag, commit, checksum, worker_pid, run_id, attempt):
    sys.stdout.write(value + "\0")
PY
    )
    [[ "${#REQUEST_FIELDS[@]}" -eq 8 ]] || fail "request field count is invalid"
    TRANSACTION_ID="${REQUEST_FIELDS[0]}"
    RELEASE_VERSION="${REQUEST_FIELDS[1]}"
    RELEASE_TAG="${REQUEST_FIELDS[2]}"
    RELEASE_COMMIT="${REQUEST_FIELDS[3]}"
    ARTIFACT_SHA256="${REQUEST_FIELDS[4]}"
    WORKER_PID="${REQUEST_FIELDS[5]}"
    GITHUB_RUN_ID="${REQUEST_FIELDS[6]}"
    GITHUB_RUN_ATTEMPT="${REQUEST_FIELDS[7]}"
}

validate_local_runner_inventory() {
    local output="$1"
    RUNNER_ROOT="${QUALIFIED_RUNNER_ROOT}" \
    RUNNER_SCHEDULE_CONFIG="${QUALIFIED_SCHEDULE_SOURCE}" \
    RUNNER_SCHEDULER_STATE="${TRANSACTION_DIR}/preflight-state.json" \
        /usr/bin/python3 "${STAGE_DIR}/deploy/runner-scheduler.py" --json > "${output}"
    /usr/bin/python3 - "${output}" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if state.get("installed") != 8:
    raise SystemExit("OGLaptop local runner inventory is incomplete")
if state.get("desired") != 4 or state.get("reason") not in {"weekday-day", "weekend-day"}:
    raise SystemExit("qualified deployment is limited to the four-runner daytime window")
runners = state.get("runners")
if not isinstance(runners, list) or [item.get("num") for item in runners] != list(range(1, 9)):
    raise SystemExit("OGLaptop runner numbering is not exactly 1 through 8")
busy = [item.get("num") for item in runners if item.get("busy")]
active = [item.get("num") for item in runners if item.get("active")]
if busy != [1]:
    raise SystemExit("only the exact deployment runner may be busy")
if active != [1, 2, 3, 4]:
    raise SystemExit("daytime qualification requires only runners 1 through 4 active")
PY
}

install_schedule_and_units() {
    install -d -o "${QUALIFIED_RUNNER_USER}" -g "${QUALIFIED_RUNNER_USER}" -m 0700 \
        "${QUALIFIED_CONFIG_DIR}"
    install -o "${QUALIFIED_RUNNER_USER}" -g "${QUALIFIED_RUNNER_USER}" -m 0644 \
        "${QUALIFIED_SCHEDULE_SOURCE}" "${QUALIFIED_SCHEDULE}"
    install -d -o root -g root -m 0755 /etc/systemd/system/runner-dashboard.service.d
    cat > /etc/systemd/system/runner-dashboard.service.d/30-qualified-capacity.conf <<EOF
[Service]
Environment=NUM_RUNNERS=4
Environment=MAX_RUNNERS=8
Environment=RUNNER_SCHEDULE_CONFIG=${QUALIFIED_SCHEDULE_SOURCE}
Environment=RUNNER_SCHEDULER_BIN=${QUALIFIED_SCHEDULER_BIN}
EOF
    chmod 0644 /etc/systemd/system/runner-dashboard.service.d/30-qualified-capacity.conf

    cat > /etc/systemd/system/runner-scheduler.service <<EOF
[Unit]
Description=Apply the qualified OGLaptop runner schedule
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
Environment=RUNNER_ROOT=${QUALIFIED_RUNNER_ROOT}
Environment=RUNNER_SCHEDULE_CONFIG=${QUALIFIED_SCHEDULE_SOURCE}
ExecStart=${QUALIFIED_SCHEDULER_RUNTIME}/bin/python ${QUALIFIED_SCHEDULER_BIN} --apply
EOF
    chmod 0644 /etc/systemd/system/runner-scheduler.service

    cat > /etc/systemd/system/runner-scheduler.timer <<'EOF'
[Unit]
Description=Apply the qualified OGLaptop schedule every five minutes

[Timer]
OnBootSec=2m
OnUnitActiveSec=5m
AccuracySec=30s
Persistent=true

[Install]
WantedBy=timers.target
EOF
    chmod 0644 /etc/systemd/system/runner-scheduler.timer
}

verify_systemd_authority() {
    local dashboard_user dashboard_workdir dashboard_exec environment scheduler_exec scheduler_config
    dashboard_user="$(systemctl show runner-dashboard.service --property=User --value)"
    dashboard_workdir="$(systemctl show runner-dashboard.service --property=WorkingDirectory --value)"
    dashboard_exec="$(systemctl show runner-dashboard.service --property=ExecStart --value)"
    environment="$(systemctl show runner-dashboard.service --property=Environment --value)"
    [[ "${dashboard_user}" == "${QUALIFIED_RUNNER_USER}" ]] || fail "dashboard service user drifted"
    [[ "${dashboard_workdir}" == "${QUALIFIED_DEPLOY_DIR}" ]] || fail "dashboard working directory drifted"
    [[ "${dashboard_exec}" == *"${QUALIFIED_DEPLOY_DIR}/.venv/bin/python"* ]] \
        || fail "dashboard is not using the governed virtual environment"
    [[ "${environment}" == *"NUM_RUNNERS=4"* && "${environment}" == *"MAX_RUNNERS=8"* ]] \
        || fail "dashboard capacity environment is not 4/8"
    scheduler_exec="$(systemctl show runner-scheduler.service --property=ExecStart --value)"
    scheduler_config="$(systemctl show runner-scheduler.service --property=Environment --value)"
    [[ "${scheduler_exec}" == *"${QUALIFIED_SCHEDULER_RUNTIME}/bin/python ${QUALIFIED_SCHEDULER_BIN} --apply"* ]] \
        || fail "scheduler is not using the root-owned release runtime"
    [[ "${scheduler_config}" == *"RUNNER_SCHEDULE_CONFIG=${QUALIFIED_SCHEDULE_SOURCE}"* ]] \
        || fail "scheduler is not using the root-owned canonical schedule"
    verify_root_owned_scheduler_runtime
    [[ "$(systemctl is-active runner-autoscaler.service 2>/dev/null || true)" != "active" ]] \
        || fail "runner autoscaler remains active"
    [[ "$(systemctl is-enabled runner-autoscaler.service 2>/dev/null || true)" != "enabled" ]] \
        || fail "runner autoscaler remains enabled"
}

verify_deployment_identity() {
    /usr/bin/python3 - "${QUALIFIED_DEPLOY_DIR}/deployment.json" "${RELEASE_VERSION}" \
        "${RELEASE_COMMIT}" "${ARTIFACT_SHA256}" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if metadata.get("version") != sys.argv[2] or metadata.get("git_sha") != sys.argv[3]:
    raise SystemExit("deployed identity does not match the qualified release")
if metadata.get("compatibility", {}).get("artifact_schema") != "runner-dashboard-artifact-v2":
    raise SystemExit("deployed artifact schema is not v2")
PY
    local version_response="${TRANSACTION_DIR}/api-version.json"
    curl -fsS --max-time 10 http://127.0.0.1:8321/health > /dev/null
    curl -fsS --max-time 10 http://127.0.0.1:8321/livez > /dev/null
    curl -fsS --max-time 10 http://127.0.0.1:8321/api/version > "${version_response}"
    /usr/bin/python3 - "${version_response}" "${RELEASE_VERSION}" "${RELEASE_COMMIT}" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("dashboard") != sys.argv[2] or payload.get("git_sha") != sys.argv[3]:
    raise SystemExit("live dashboard identity does not match the qualified release")
PY
}

verify_scheduler_steady_state() {
    local state_file="$1"
    /usr/bin/python3 - "${state_file}" <<'PY'
import json
import sys
from pathlib import Path

state = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
runners = state.get("runners", [])
if state.get("desired") != 4 or state.get("reason") not in {"weekday-day", "weekend-day"}:
    raise SystemExit("scheduler did not retain daytime desired capacity")
if state.get("actions") != []:
    raise SystemExit("scheduler touched a runner unit during qualification")
if [item.get("num") for item in runners if item.get("active")] != [1, 2, 3, 4]:
    raise SystemExit("scheduler steady state is not runners 1 through 4")
if [item.get("num") for item in runners if item.get("busy")] != [1]:
    raise SystemExit("a non-deployment runner became busy")
PY
}

wait_for_scheduler_cycle() {
    local first_invocation="$1"
    local elapsed=0
    local current=""
    while (( elapsed < SCHEDULER_CYCLE_TIMEOUT_SECONDS )); do
        sleep 10
        elapsed=$((elapsed + 10))
        current="$(systemctl show runner-scheduler.service --property=InvocationID --value)"
        if [[ -n "${current}" && "${current}" != "${first_invocation}" ]]; then
            SCHEDULER_CYCLE_SECONDS="${elapsed}"
            return 0
        fi
    done
    fail "a complete five-minute scheduler cycle was not observed"
}

write_redacted_evidence() {
    local status="$1"
    local result="$2"
    local evidence="${INBOX_DIR}/${QUALIFIED_EVIDENCE_NAME}"
    local before_digest="unavailable"
    local after_digest="unavailable"
    [[ ! -f "${MUTABLE_BEFORE}" ]] || before_digest="$(sha256sum "${MUTABLE_BEFORE}" | awk '{print $1}')"
    [[ ! -f "${MUTABLE_AFTER}" ]] || after_digest="$(sha256sum "${MUTABLE_AFTER}" | awk '{print $1}')"
    /usr/bin/python3 - "${evidence}" "${status}" "${result}" "${TRANSACTION_ID}" \
        "${RELEASE_VERSION}" "${RELEASE_TAG}" "${RELEASE_COMMIT}" "${ARTIFACT_SHA256}" \
        "${before_digest}" "${after_digest}" "${ROLLBACK_RESULT}" \
        "${SCHEDULER_CYCLE_SECONDS:-0}" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

payload = {
    "schema": "qualified-release-evidence-v1",
    "generated_at": datetime.now(UTC).isoformat(),
    "status": sys.argv[2],
    "result": sys.argv[3],
    "transaction_id": sys.argv[4],
    "target": "OGLaptop",
    "release": {"version": sys.argv[5], "tag": sys.argv[6], "commit": sys.argv[7], "sha256": sys.argv[8]},
    "worker_exception": {"runner": "d-sorg-local-Oglaptop-1", "busy_workers": [1]},
    "mutable_manifest": {"before_sha256": sys.argv[9], "after_sha256": sys.argv[10]},
    "rollback": sys.argv[11],
    "scheduler": {"desired": 4, "active": [1, 2, 3, 4], "actions": [], "cycle_seconds": int(sys.argv[12])},
    "health": {"health": status == "committed", "livez": status == "committed"},
}
Path(sys.argv[1]).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
    chown root:"${QUALIFIED_RUNNER_USER}" "${evidence}"
    chmod 0640 "${evidence}"
}

transaction_exit() {
    local exit_code=$?
    trap - EXIT
    set +e
    if (( exit_code != 0 && MUTATION_STARTED == 1 && TRANSACTION_COMMITTED == 0 )); then
        journal_event "rollback-started" "pending"
        if restore_rollback_snapshot; then
            ROLLBACK_RESULT="restored"
            journal_event "rollback-completed" "ok"
        else
            ROLLBACK_RESULT="failed"
            journal_event "rollback-completed" "failed"
        fi
    fi
    if (( exit_code != 0 )); then
        write_redacted_evidence "rejected-or-rolled-back" "failure"
    fi
    rm -f "${REQUEST_FILE}"
    exit "${exit_code}"
}

[[ "${EUID}" -eq 0 ]] || fail "qualified deployment helper must run as root"
[[ "${SUDO_USER:-}" == "${QUALIFIED_RUNNER_USER}" ]] || fail "qualified helper requires the OGLaptop runner user"
[[ -x "${COSIGN_BIN}" && "$(stat -c '%U:%G' "${COSIGN_BIN}")" == "root:root" ]] \
    || fail "root-owned cosign authority is unavailable"
verify_root_authority_file "${QUALIFIED_SCHEDULE_SOURCE}"

REQUEST_FILE="$(mktemp "${QUALIFIED_STATE_ROOT}/request.XXXXXX")"
chmod 0600 "${REQUEST_FILE}"
head -c 16384 > "${REQUEST_FILE}"
[[ ! -s "${REQUEST_FILE}" || "$(stat -c '%s' "${REQUEST_FILE}")" -lt 16384 ]] \
    || fail "request exceeds the size bound"
read_request "${REQUEST_FILE}"

QUALIFIED_SCHEDULER_RELEASE="${QUALIFIED_RELEASE_ROOT}/${RELEASE_VERSION}-${RELEASE_COMMIT}"
QUALIFIED_SCHEDULER_RUNTIME="${QUALIFIED_SCHEDULER_RELEASE}/runtime"
QUALIFIED_SCHEDULER_BIN="${QUALIFIED_SCHEDULER_RELEASE}/runner-scheduler.py"

INBOX_DIR="${QUALIFIED_INBOX_ROOT}/${TRANSACTION_ID}"
TRANSACTION_DIR="${QUALIFIED_TRANSACTION_ROOT}/${TRANSACTION_ID}"
[[ -d "${INBOX_DIR}" && ! -L "${INBOX_DIR}" ]] || fail "transaction inbox is absent"
[[ "$(stat -c '%U' "${INBOX_DIR}")" == "${QUALIFIED_RUNNER_USER}" ]] \
    || fail "transaction inbox has an unexpected owner"
[[ ! -e "${TRANSACTION_DIR}" ]] || fail "transaction identity was already used"
install -d -o root -g root -m 0700 "${TRANSACTION_DIR}"
TRANSACTION_JOURNAL="${TRANSACTION_DIR}/journal.jsonl"
ROLLBACK_SNAPSHOT="${TRANSACTION_DIR}/rollback"
MUTABLE_BEFORE="${TRANSACTION_DIR}/mutable-before.json"
MUTABLE_AFTER="${TRANSACTION_DIR}/mutable-after.json"
MUTABLE_PAYLOAD="${TRANSACTION_DIR}/mutable-payload"
MUTABLE_AFTER_PAYLOAD="${TRANSACTION_DIR}/mutable-after-payload"
ARTIFACT_COPY="${TRANSACTION_DIR}/dashboard-${RELEASE_VERSION}.tar.gz"
BUNDLE_COPY="${TRANSACTION_DIR}/dashboard-${RELEASE_VERSION}.bundle"
STAGE_DIR="${TRANSACTION_DIR}/stage"
trap transaction_exit EXIT

journal_event "request-accepted"
validate_worker_ancestry "${WORKER_PID}"
copy_inbox_asset "${INBOX_DIR}/dashboard-${RELEASE_VERSION}.tar.gz" "${ARTIFACT_COPY}"
copy_inbox_asset "${INBOX_DIR}/dashboard-${RELEASE_VERSION}.bundle" "${BUNDLE_COPY}"
verify_sha256 "${ARTIFACT_COPY}" "${ARTIFACT_SHA256}"
verify_cosign_bundle "${ARTIFACT_COPY}" "${BUNDLE_COPY}"
validate_archive "${ARTIFACT_COPY}"
install -d -o root -g root -m 0700 "${STAGE_DIR}"
tar -xzf "${ARTIFACT_COPY}" --no-same-owner --no-same-permissions -C "${STAGE_DIR}"
verify_root_only_tree "${STAGE_DIR}"
validate_artifact_metadata "${STAGE_DIR}" "${RELEASE_VERSION}" "${RELEASE_COMMIT}"
validate_local_runner_inventory "${TRANSACTION_DIR}/local-inventory-before.json"
journal_event "preflight-qualified"

record_rollback_baseline
MUTATION_STARTED=1
journal_event "begin_mutation"

quiesce_for_snapshot
create_quiesced_rollback_snapshot
create_mutable_manifest "${MUTABLE_BEFORE}" "${MUTABLE_PAYLOAD}"
verify_quiesced_snapshot
journal_event "quiesced-snapshot-verified"
verify_sha256 "${ARTIFACT_COPY}" "${ARTIFACT_SHA256}"
DEPLOY_DIR="${QUALIFIED_DEPLOY_DIR}" bash "${STAGE_DIR}/deploy/install-dashboard-artifact.sh" \
    --artifact "${ARTIFACT_COPY}" \
    --checksum "${ARTIFACT_SHA256}" \
    --deploy-dir "${QUALIFIED_DEPLOY_DIR}"
verify_sha256 "${ARTIFACT_COPY}" "${ARTIFACT_SHA256}"
chown -R "${QUALIFIED_RUNNER_USER}:${QUALIFIED_RUNNER_USER}" "${QUALIFIED_DEPLOY_DIR}"
restore_mutable_manifest "${MUTABLE_BEFORE}" "${MUTABLE_PAYLOAD}"
verify_mutable_manifest "${MUTABLE_BEFORE}"
create_mutable_manifest "${MUTABLE_AFTER}" "${MUTABLE_AFTER_PAYLOAD}"
cmp -s "${MUTABLE_BEFORE}" "${MUTABLE_AFTER}" || fail "mutable-state manifest changed during installation"
rm -rf -- "${MUTABLE_AFTER_PAYLOAD}"
[[ "$("${QUALIFIED_DEPLOY_DIR}/.venv/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.12" ]] \
    || fail "deployed governed runtime is not Python 3.12"
journal_event "artifact-installed"

install_root_owned_scheduler_runtime
install_schedule_and_units
systemctl daemon-reload
systemctl restart runner-dashboard.service
for _attempt in $(seq 1 30); do
    systemctl is-active --quiet runner-dashboard.service \
        && curl -fsS --max-time 3 http://127.0.0.1:8321/livez > /dev/null \
        && break
    sleep 2
done
systemctl is-active --quiet runner-dashboard.service || fail "dashboard service did not become active"
verify_systemd_authority
verify_deployment_identity
journal_event "dashboard-qualified"

systemctl start runner-scheduler.service
verify_scheduler_steady_state /var/lib/runner-scheduler/state.json
FIRST_INVOCATION="$(systemctl show runner-scheduler.service --property=InvocationID --value)"
[[ -n "${FIRST_INVOCATION}" ]] || fail "scheduler invocation identity is absent"
systemctl enable --now runner-scheduler.timer
wait_for_scheduler_cycle "${FIRST_INVOCATION}"
verify_scheduler_steady_state /var/lib/runner-scheduler/state.json
verify_systemd_authority
verify_deployment_identity
journal_event "scheduler-cycle-qualified"

write_redacted_evidence "committed" "success"
journal_event "transaction-committed"
TRANSACTION_COMMITTED=1
