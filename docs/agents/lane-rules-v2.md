# Lane rules v2 (all agent lanes)

Adopted 2026-11-03 per Dieter. Evidence: issues #3962 (launch guard) and #3963 (size gate).

## Cursor cloud agent — standard launch prompt v2

Repo: {repo} Issue: #{issue} Lane: cursor/{issue}-{slug} 0. PRE-FLIGHT (abort and comment "skipped: <reason>" on the issue if any is true):

- repo is drained or on hold (check RM staff/roles/\*.yml holds / /api/staff/holds via the issue's labels `hold`, `drain`)
- `gh pr list --search "#{issue} in:body,title" --state open` returns any PR from ANY agent
- issue has a live claim comment/label newer than 4h from another lane

1. CLAIM: comment "claimed by cursor-cloud <run id>, ETA 2h" before writing code.
2. SCOPE: one issue, one PR. Prefer a small, cohesive diff (generated files and lockfiles count in advisory tooling). Do **not** split a cohesive fix only to hit a line count — ship the work that matches the issue.
3. TDD: failing test first, then fix. Run the repo's lint+type+test commands locally in the VM before pushing.
4. PR: body contains "Closes #{issue}", test evidence, and line count. No stacked PRs. Do NOT arm auto-merge, do NOT force-push, no --no-verify.
5. CI red: fix up to 2 times on the same branch; then comment the failing check + log excerpt on the PR and stop.
6. Never close/edit other agents' PRs, never touch queued PRs, never touch secrets or workflows under .github/ unless the issue says so.
7. Finish in one session; if blocked, comment the blocker on the issue and stop (no draft left open > 24h).

## agy / gemini lane standing prompt v2

- Same PRE-FLIGHT + CLAIM as cursor v2 (open-PR-from-any-agent check, drain/hold check).
- When nothing is claimable: exit and report "idle: nothing claimable" ONCE; sleep >=30 min before re-polling (no tight loops).
- One PR per issue; prefer cohesive scope; TDD.
- Open-PR cap: max 5 open PRs per lane per repo; past that, fix CI on your own open PRs instead of opening new ones.
- On usage-limit error: stop, record reset time, do not retry before it.

## claude / codex lane standing prompt v2

- Same PRE-FLIGHT/CLAIM/SCOPE rules as cursor v2.
- Codex: on usage-limit, halt the whole lane until reset (no failing-run loop); on resume, first rebase/finish own stale PRs (>72h) before new work.

## Branch naming

Every lane prefixes branches with its lane id (`cursor/`, `agy-<x>/`, `gemini/`, `claude/`, `codex/`, `jules/`) so PRs can be attributed even when opened by the fleet bot. Grok CLI runs under staff dispatch without a dedicated `grok/` lane prefix unless introduced.
