#!/usr/bin/env bash
# Root-only release-specific scheduler runtime primitives for issue #1138.

verify_root_authority_file() {
    local path="$1"
    [[ -f "${path}" && ! -L "${path}" ]] || fail "root authority is not a regular file"
    [[ "$(stat -c '%U:%G' "${path}")" == "root:root" ]] || fail "file is not root-owned"
    (( (8#$(stat -c '%a' "${path}") & 8#022) == 0 )) || fail "file is group/other writable"
}

verify_root_only_tree() {
    local root="$1"
    [[ -d "${root}" && ! -L "${root}" ]] || fail "root authority tree is absent"
    [[ -z "$(find "${root}" ! -user root -print -quit)" ]] || fail "tree contains a non-root-owned path"
    [[ -z "$(find "${root}" ! -type l -perm /022 -print -quit)" ]] \
        || fail "tree contains a group/other-writable path"
}

verify_root_owned_scheduler_runtime() {
    verify_root_only_tree "${QUALIFIED_SCHEDULER_RELEASE}"
    [[ -z "$(find "${QUALIFIED_SCHEDULER_RELEASE}" -type l -print -quit)" ]] \
        || fail "scheduler runtime contains a symlink"
    verify_root_authority_file "${QUALIFIED_SCHEDULER_BIN}"
    local resolved_python
    resolved_python="$(readlink -f "${QUALIFIED_SCHEDULER_RUNTIME}/bin/python")"
    [[ "${resolved_python}" == "${QUALIFIED_SCHEDULER_RUNTIME}/bin/python" ]] \
        || fail "scheduler interpreter escaped the release runtime"
    verify_root_authority_file "${resolved_python}"
    [[ -x "${resolved_python}" ]] || fail "scheduler interpreter is not executable"
    [[ "$("${QUALIFIED_SCHEDULER_RUNTIME}/bin/python" -c \
        'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.12" ]] \
        || fail "root-owned scheduler runtime is not Python 3.12"
}

install_root_owned_scheduler_runtime() {
    local candidate="${TRANSACTION_DIR}/scheduler-release"
    [[ "$(/usr/bin/python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" == "3.12" ]] \
        || fail "system scheduler authority is not Python 3.12"
    install -d -o root -g root -m 0755 "${QUALIFIED_RELEASE_ROOT}"
    install -d -o root -g root -m 0700 "${candidate}"
    /usr/bin/python3 -m venv --copies --without-pip "${candidate}/runtime"
    install -o root -g root -m 0555 "${STAGE_DIR}/deploy/runner-scheduler.py" \
        "${candidate}/runner-scheduler.py"
    chown -hR root:root "${candidate}"
    find "${candidate}" -type d -exec chmod 0555 {} +
    find "${candidate}" -type f -exec chmod 0444 {} +
    chmod 0555 "${candidate}"/runtime/bin/python*
    chmod 0555 "${candidate}/runner-scheduler.py"
    if [[ -e "${QUALIFIED_SCHEDULER_RELEASE}" ]]; then
        verify_root_owned_scheduler_runtime
        cmp -s "${candidate}/runner-scheduler.py" "${QUALIFIED_SCHEDULER_BIN}" \
            || fail "existing release-specific scheduler differs from the signed artifact"
        rm -rf -- "${candidate}"
    else
        mv -- "${candidate}" "${QUALIFIED_SCHEDULER_RELEASE}"
    fi
    verify_root_owned_scheduler_runtime
}
