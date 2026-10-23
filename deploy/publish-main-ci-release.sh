#!/usr/bin/env bash
# Publish a main CI auto-release for an exact commit SHA (idempotent).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHA=""
COMMITTER_DATE=""
KEEP_RELEASES=30
PROTECTED_SHAS=""
DRY_RUN=false
REQUIRE_CURRENT_MAIN=false

usage() {
  cat <<'EOF'
Usage: publish-main-ci-release.sh --sha SHA --committer-date ISO8601 [options]

Options:
  --sha SHA                 Full 40-character main commit to release
  --committer-date DATE     commit.committer.date from GitHub (UTC)
  --keep N                  Retain newest N auto-releases (default: 30)
  --protected-sha LIST      Comma-separated SHAs never deleted during retention
  --require-current-main    Skip when this SHA is no longer the current main head
  --dry-run                 Plan only; do not create tag/release or delete old rows
  -h, --help                Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha) SHA="$2"; shift 2 ;;
    --committer-date) COMMITTER_DATE="$2"; shift 2 ;;
    --keep) KEEP_RELEASES="$2"; shift 2 ;;
    --protected-sha) PROTECTED_SHAS="$2"; shift 2 ;;
    --require-current-main) REQUIRE_CURRENT_MAIN=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo "sha must be 40 lowercase hex" >&2; exit 1; }
[[ -n "$COMMITTER_DATE" ]] || { echo "committer-date is required" >&2; exit 1; }

export PYTHONPATH="${REPO_ROOT}/deploy${PYTHONPATH:+:$PYTHONPATH}"
WORK_DIR="${RUNNER_TEMP:-$(mktemp -d)}"
CHECK_JSON="${WORK_DIR}/check-runs-${SHA}.json"
RELEASES_JSON="${WORK_DIR}/releases.json"

GATE_ATTEMPTS="${GATE_ATTEMPTS:-90}"
GATE_SLEEP_SECS="${GATE_SLEEP_SECS:-60}"
gate_ok=false

skip_if_main_moved() {
  [[ "$REQUIRE_CURRENT_MAIN" == "true" ]] || return 0
  current_main_sha="$(gh api "repos/${GITHUB_REPOSITORY}/commits/main" --jq '.sha')"
  [[ "$current_main_sha" =~ ^[0-9a-f]{40}$ ]] || {
    echo "GitHub returned an invalid current-main SHA; refusing release" >&2
    exit 1
  }
  if [[ "$current_main_sha" != "$SHA" ]]; then
    echo "Main CI release candidate ${SHA:0:7} was superseded by ${current_main_sha:0:7}; skipping release"
    exit 0
  fi
}

for attempt in $(seq 1 "$GATE_ATTEMPTS"); do
  skip_if_main_moved
  gh api "repos/${GITHUB_REPOSITORY}/commits/${SHA}/check-runs?per_page=100" --paginate --slurp > "$CHECK_JSON"
  gate_status=0
  python3 "${REPO_ROOT}/deploy/main_auto_release/publish_cli.py" verify-gate \
    --publisher --sha "$SHA" --check-runs-json "$CHECK_JSON" || gate_status=$?
  case "$gate_status" in
    0)
      gate_ok=true
      break
      ;;
    1)
      ;;
    2)
      echo "CI gate has terminal failures for ${SHA:0:7}; skipping release"
      exit 0
      ;;
    3)
      echo "CI gate response is invalid or incomplete for ${SHA:0:7}; refusing release" >&2
      exit 3
      ;;
    *)
      echo "CI gate verifier failed unexpectedly with status ${gate_status}; refusing release" >&2
      exit "$gate_status"
      ;;
  esac
  if [[ "$attempt" -eq "$GATE_ATTEMPTS" ]]; then
    echo "CI gate not satisfied for ${SHA:0:7} after ${GATE_ATTEMPTS} attempts; skipping release"
    exit 0
  fi
  echo "CI gate waiting (attempt ${attempt}/${GATE_ATTEMPTS}); sleeping ${GATE_SLEEP_SECS}s"
  sleep "$GATE_SLEEP_SECS"
done
if [[ "$gate_ok" != "true" ]]; then
  exit 0
fi

skip_if_main_moved

gh api "repos/${GITHUB_REPOSITORY}/releases?per_page=100" > "$RELEASES_JSON"
if python3 "${REPO_ROOT}/deploy/main_auto_release/publish_cli.py" should-skip \
  --sha "$SHA" --releases-json "$RELEASES_JSON"; then
  echo "Release already published for ${SHA:0:7}"
  exit 0
fi

PLAN="$(
  python3 "${REPO_ROOT}/deploy/main_auto_release/publish_cli.py" plan-tag \
    --sha "$SHA" --committer-date "$COMMITTER_DATE"
)"
TAG="$(grep '^tag=' <<< "$PLAN" | cut -d= -f2-)"
VERSION="$(grep '^version=' <<< "$PLAN" | cut -d= -f2-)"
NOTES_HEADER="$(grep '^notes_header=' <<< "$PLAN" | cut -d= -f2-)"

if [[ "$DRY_RUN" == "true" ]]; then
  echo "DRY RUN: would publish ${TAG} for ${SHA} (dashboard-${VERSION}.tar.gz)"
  exit 0
fi

OUTPUT_DIR="${WORK_DIR}/release-artifacts"
bash "${REPO_ROOT}/deploy/package-dashboard-artifact.sh" \
  --output-dir "$OUTPUT_DIR" \
  --version "$VERSION" \
  --sha "$SHA" \
  --python-minor "3.12"

ARTIFACT="$OUTPUT_DIR/dashboard-${VERSION}.tar.gz"
CHECKSUM="${ARTIFACT}.sha256"
SHA256="$(awk '{print $1}' "$CHECKSUM")"

NOTES_FILE="${WORK_DIR}/release-notes.md"
{
  echo "# ${TAG}"
  echo
  echo "_${NOTES_HEADER}_"
  echo
  echo "- commit: \`${SHA}\`"
  echo "- artifact sha256: \`${SHA256}\`"
} > "$NOTES_FILE"

skip_if_main_moved

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git tag -a "$TAG" -m "${NOTES_HEADER}" "$SHA"
git push origin "$TAG"

gh release create "$TAG" \
  --repo "$GITHUB_REPOSITORY" \
  --title "$TAG" \
  --notes-file "$NOTES_FILE" \
  --target "$SHA" \
  "$ARTIFACT" \
  "$CHECKSUM"

echo "Published ${TAG} (${SHA:0:7}) sha256=${SHA256}"

gh api "repos/${GITHUB_REPOSITORY}/releases?per_page=100" > "$RELEASES_JSON"
while IFS= read -r old_tag; do
  [[ -n "$old_tag" ]] || continue
  echo "Pruning old auto-release ${old_tag}"
  gh release delete "$old_tag" --repo "$GITHUB_REPOSITORY" --yes
  git push --delete origin "$old_tag" || true
done < <(
  python3 "${REPO_ROOT}/deploy/main_auto_release/publish_cli.py" prune-plan \
    --releases-json "$RELEASES_JSON" \
    --keep "$KEEP_RELEASES" \
    --protected-sha "$PROTECTED_SHAS"
)
