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

post_body 3700 "$ROOT/docs/agents/issue-specs/3700-george-persona-port.md"
post_body 3702 "$ROOT/docs/agents/issue-specs/3702-linus-persona-port.md"
comment_body 3704 "$ROOT/docs/agents/issue-specs/3704-cutover-tracking.md"
post_body 3822 "$ROOT/docs/agents/issue-specs/3822-missed-run-alert.md"
post_body 3823 "$ROOT/docs/agents/issue-specs/3823-agent-bus-reaper-readyz.md"
post_body 3852 "$ROOT/docs/agents/issue-specs/3852-desk-role-self-test.md"
post_body 3856 "$ROOT/docs/agents/issue-specs/3856-coordination-job-releases.md"
post_body 3883 "$ROOT/docs/agents/issue-specs/3883-staff-console-mobile-drawer-focus.md"
apply_labels
echo "Done. Verify pickable CLI issues: gh issue list --repo $REPO --label tier:cli --label judgement:objective --state open"
