#!/usr/bin/env bash
# Post lane-facing specs and taxonomy labels to GitHub (requires issue write).
# Cloud fleet bot lacks updateIssue/addLabels; run on Desk hub as Dieter:
#   gh-personal issue edit …   OR grant the fleet App issues:write on RD.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${RD_LABEL_REPO:-D-sorganization/Runner_Dashboard}"

post_body() {
  local num="$1" file="$2"
  echo "Updating #$num body from $file"
  gh issue edit "$num" --repo "$REPO" --body-file "$file"
}

comment_body() {
  local num="$1" file="$2"
  echo "Comment on #$num from $file"
  gh issue comment "$num" --repo "$REPO" --body-file "$file"
}

apply_labels() {
  bash "$ROOT/scripts/apply_rd_issue_labels.sh"
}

post_body 3700 "$ROOT/docs/agents/issue-specs/github-bodies/github-issue-3700-body.md"
post_body 3702 "$ROOT/docs/agents/issue-specs/github-bodies/github-issue-3702-body.md"
comment_body 3704 "$ROOT/docs/agents/issue-specs/3704-cutover-tracking.md"
for n in 3822 3823 3852 3856 3883; do
  post_body "$n" "$ROOT/docs/agents/issue-specs/github-bodies/github-issue-${n}-body.md"
done
apply_labels
echo "Done. Verify pickable CLI issues: gh issue list --repo $REPO --label tier:cli --label judgement:objective --state open"
