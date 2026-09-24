#!/usr/bin/env bash
# Staff Hub node acceptance (Runner_Dashboard#1273): one pass/fail check that a
# node is fully set up as a Staff Hub worker. Run inside the node's WSL distro as
# the dashboard user (from another host: via _deploy/remote/run_task.py, never
# wsl.exe over SSH). Read-only except for --dispatch, which starts one harmless
# ad-hoc run per provider through the local dashboard (loopback).
#
#   bash deploy/staff-node-acceptance.sh --scheduler off            # checks only
#   bash deploy/staff-node-acceptance.sh --scheduler on --dispatch  # + provider runs
#
# Exit 0 when every check passes; 1 otherwise. The last line is a JSON summary.
set -uo pipefail

SCHEDULER=""
DISPATCH=0
EXPECT_SHA=""
PROVIDERS="claude codex antigravity cursor-agent ollama claude-ollama"
BASE="${DASHBOARD_URL:-http://127.0.0.1:8321}"
while [ $# -gt 0 ]; do
    case "$1" in
        --scheduler) SCHEDULER="$2"; shift 2 ;;
        --dispatch) DISPATCH=1; shift ;;
        --expect-sha) EXPECT_SHA="$2"; shift 2 ;;
        --providers) PROVIDERS="$2"; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done
case "$SCHEDULER" in on|off) ;; *) echo "--scheduler on|off is required (only DeskComputer schedules)" >&2; exit 2 ;; esac

export PATH="$HOME/.local/bin:$PATH"
[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1
CONF="$HOME/.config/runner-dashboard"
FAILS=0
PASSES=0

pass() { PASSES=$((PASSES + 1)); printf 'PASS  %-28s %s\n' "$1" "${2:-}"; }
fail() { FAILS=$((FAILS + 1)); printf 'FAIL  %-28s %s\n' "$1" "${2:-}"; }
check() { if [ "$2" = 1 ]; then pass "$1" "${3:-}"; else fail "$1" "${3:-}"; fi; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }
envval() { grep -E "^$1=" "$CONF/env" 2>/dev/null | tail -1 | cut -d= -f2-; }

echo "== $(hostname) $(date -Is)"

# ── dashboard ────────────────────────────────────────────────────────────
health=$(curl -s -m 15 "$BASE/api/health")
status=$(printf '%s' "$health" | json 'd.get("status","")')
sha=$(printf '%s' "$health" | json 'd.get("deployment",{}).get("git_sha","")[:7]')
check dashboard-healthy "$([ "$status" = healthy ] && echo 1)" "status=$status sha=$sha"
if [ -n "$EXPECT_SHA" ]; then
    check deployed-commit "$([ "$sha" = "${EXPECT_SHA:0:7}" ] && echo 1)" "have=$sha want=${EXPECT_SHA:0:7}"
fi

# ── sign-ins (no secrets printed) ────────────────────────────────────────
gh_ok=$(env -u GH_TOKEN -u GITHUB_TOKEN gh auth status 2>&1 | grep -c 'Logged in')
check gh-signed-in "$([ "$gh_ok" -ge 1 ] && echo 1)"
creds="$(envval CLAUDE_CONFIG_DIR)/.credentials.json"
check claude-service-seat "$([ -s "$creds" ] && echo 1)" "$creds"
check codex-signed-in "$(timeout 20 codex login status </dev/null 2>&1 | grep -q 'Logged in' && echo 1)" "$(codex --version 2>/dev/null)"
check cursor-signed-in "$(timeout 20 cursor-agent status </dev/null 2>&1 | grep -q 'Logged in' && echo 1)"

# ── service configuration ────────────────────────────────────────────────
dropin=$(cat /etc/systemd/system/runner-dashboard.service.d/*.conf 2>/dev/null)
check dropin-cursor-paths "$(printf '%s' "$dropin" | grep -q '\.config/cursor' && echo 1)"
# The service's own PATH is checked by provider-installed:* below (shutil.which inside the unit).
gitcfg=$(envval GIT_CONFIG_GLOBAL)
check staff-gitconfig "$([ -n "$gitcfg" ] && [ -s "$gitcfg" ] && echo 1)" "$gitcfg"
rm_root=$(envval STAFF_RM_ROOT)
check rm-root-live-clone "$([ "$rm_root" = "$HOME/staff-repos/Repository_Management" ] && echo 1)" "$rm_root"
repos=$(ls -d "$HOME"/staff-repos/*/.git 2>/dev/null | wc -l)
check staff-repos-cloned "$([ "$repos" -ge 7 ] && echo 1)" "$repos/7"
# Enabled = linked into timers.target.wants. Not `systemctl --user`: some WSL distros have no user D-Bus
# socket, so it fails from S4U tasks even while the timer runs. rm-source-fresh proves it actually fires.
check rm-sync-timer-enabled "$([ -L "$HOME/.config/systemd/user/timers.target.wants/runner-dashboard-rm-sync.timer" ] && echo 1)"
check user-lingering "$(loginctl show-user "$USER" -p Linger 2>/dev/null | grep -qx 'Linger=yes' && echo 1)"

# ── staff state ──────────────────────────────────────────────────────────
board=$(curl -s -m 30 "$BASE/api/staff/board?local=1")
rm_status=$(printf '%s' "$board" | json '(d.get("rm_source") or {}).get("status","")')
rm_age=$(printf '%s' "$board" | json '(d.get("rm_source") or {}).get("check_age_seconds") or 999999')
check rm-source-fresh "$([ "$rm_status" = updated ] || [ "$rm_status" = unchanged ] && [ "${rm_age%.*}" -lt 3600 ] && echo 1)" "status=$rm_status check_age=${rm_age%.*}s"
schedule=$(curl -s -m 15 "$BASE/api/staff/schedule")
sched_on=$(printf '%s' "$schedule" | json '"on" if d.get("enabled") else "off"')
check scheduler-expected "$([ "$sched_on" = "$SCHEDULER" ] && echo 1)" "have=$sched_on want=$SCHEDULER"
held=$(printf '%s' "$schedule" | json '",".join(r.get("role") or r.get("name") or "?" for r in (d.get("roles") or d.get("schedule") or []) if r.get("hold"))')
check no-held-roles "$([ -z "$held" ] && echo 1)" "${held:-none}"
for p in $PROVIDERS; do
    have=$(printf '%s' "$board" | json "str(d.get('providers',{}).get('$p', False)).lower()")
    check "provider-installed:$p" "$([ "$have" = true ] && echo 1)"
done

# ── Ollama reachability (loopback-only server behind the WSL bridge) ─────
case " $PROVIDERS " in *" ollama "*|*" claude-ollama "*)
    gw=$(ip route | awk '/default/{print $3; exit}')
    ver=$(curl -s -m 5 "http://$gw:11434/api/version" | json 'd.get("version","")')
    check ollama-via-wsl-gateway "$([ -n "$ver" ] && echo 1)" "gateway=$gw version=${ver:-unreachable}" ;;
esac

# ── one harmless ad-hoc run per provider ─────────────────────────────────
if [ "$DISPATCH" = 1 ]; then
    prompt='Health check: do not change anything. Run the shell command echo hi, reply OK, then print a final line: STAFF_RESULT: ok'
    declare -A RUN
    for p in $PROVIDERS; do
        body=$(python3 -c 'import json,sys; print(json.dumps({"provider": sys.argv[1], "machine": "local", "prompt": sys.argv[2]}))' "$p" "$prompt")
        RUN[$p]=$(curl -s -m 60 -X POST "$BASE/api/staff/ad-hoc/run" -H 'X-Requested-With: XMLHttpRequest' \
            -H 'Content-Type: application/json' -d "$body" | json 'd["run"]["id"]')
    done
    deadline=$((SECONDS + 900))
    for p in $PROVIDERS; do
        id=${RUN[$p]}
        if [ -z "$id" ]; then fail "run:$p" "dispatch refused"; continue; fi
        st=""
        while [ $SECONDS -lt $deadline ]; do
            st=$(curl -s -m 20 "$BASE/api/staff/runs/$id" | json '(d.get("run") or d).get("status","")')
            case "$st" in queued|preparing|running|"") sleep 10 ;; *) break ;; esac
        done
        err=$(curl -s -m 20 "$BASE/api/staff/runs/$id" | json '((d.get("run") or d).get("error") or "")[:80]')
        check "run:$p" "$([ "$st" = succeeded ] && echo 1)" "$id $st ${err}"
    done
fi

echo "== $PASSES passed, $FAILS failed"
printf '{"host":"%s","sha":"%s","passed":%d,"failed":%d,"dispatch":%d}\n' "$(hostname)" "$sha" "$PASSES" "$FAILS" "$DISPATCH"
[ "$FAILS" -eq 0 ]
