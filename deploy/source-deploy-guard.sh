#!/usr/bin/env bash
# Refuse legacy source-copy deployments over immutable artifact releases.
source_deploy_guard() {
    local deploy_dir="$1" resolved metadata
    if [[ -L "$deploy_dir" ]]; then
        resolved=$(readlink -f -- "$deploy_dir") || {
            echo "Refusing source deployment: cannot resolve deploy symlink $deploy_dir" >&2
            return 1
        }
        case "/$resolved/" in
            */dashboard-releases/*|*/runner-dashboard-releases/*)
                echo "Refusing source deployment into versioned artifact release: $resolved" >&2
                return 1
                ;;
        esac
    fi
    metadata="$deploy_dir/deployment.json"
    [[ -f "$metadata" ]] || return 0
    python3 - "$metadata" <<'PY'
import json
import sys
from pathlib import Path
try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError) as exc:
    print(f"Refusing source deployment: deployment metadata is unreadable ({type(exc).__name__})", file=sys.stderr)
    raise SystemExit(1)
if not isinstance(payload, dict):
    print("Refusing source deployment: deployment metadata has an invalid shape", file=sys.stderr)
    raise SystemExit(1)
compatibility = payload.get("compatibility", {})
if not isinstance(compatibility, dict):
    print("Refusing source deployment: deployment metadata has an invalid shape", file=sys.stderr)
    raise SystemExit(1)
if compatibility.get("artifact_schema") == "runner-dashboard-artifact-v2" or payload.get("source") == "github-actions-artifact-build":
    print("Refusing source deployment over artifact-backed deployment metadata", file=sys.stderr)
    raise SystemExit(1)
PY
}
