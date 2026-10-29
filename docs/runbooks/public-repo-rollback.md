# Runbook: Public Repo Rollback

Issue #3085, epic #3077 (slice 7). Cutover renames the current repo to
`Runner_Dashboard-private` (archived, read-only) and the fresh public repo
takes the `Runner_Dashboard` name. If a problem surfaces after flipping
visibility — unexpected exposure, a guardrail gap, a missed scan finding —
re-privatize quickly and triage from a safe state.

This runbook names owner-only steps explicitly. Nothing here runs
automatically against GitHub visibility; every mutating command below is
run manually by an owner.

## Symptom

- A publish-guard or secret-scan finding against the public repo's history
  or tree, found after the visibility flip.
- An unexpected fork, clone, or external exposure report post-cutover.
- A required check (merge queue, rulesets, `Publish Guard and Fork Canary`)
  is missing or misconfigured once traffic starts.

## Severity

**P1** — treat any post-cutover exposure report as real until ruled out.
RTO for re-privatizing is **15 minutes**.

## Mitigation: re-privatize within 15 minutes

```bash
# 1. Flip visibility back to private (owner-only; requires repo admin).
gh repo edit D-sorganization/Runner_Dashboard --visibility private \
  --accept-visibility-change-consequences

# 2. Confirm the flip took effect.
gh repo view D-sorganization/Runner_Dashboard --json visibility -q .visibility
```

Freeze write access while triaging (owner decision; never bypass branch
protection to do it).

## What to check afterward

- Re-run the slice-3 scan (`scripts/scan_history.py --report <private path
  outside the repo>`) against the now-private repo.
- Confirm HOLD #2657, #2658 and #2880 are still in force on repo settings —
  rollback must never silently drop a hold.
- A public-repo exposure can reach a fork, cache, or search index; scrubbing
  history does not retract that (the epic's own history decision). Rotating
  any touched credential is the real mitigation, not history editing — see
  [`public-repo-credential-rotation.md`](./public-repo-credential-rotation.md).
- Decide with the owner whether re-launch needs a tighter guardrail
  (`publish-allowlist.txt`, a new forbidden pattern) before flipping public
  again.

## Resolution

- File a P1 issue with what was found, when, and rotation evidence if a
  credential was touched.
- Add a regression case to the relevant guard (a `publish_guard.py` canary
  category, or a new forbidden pattern) so the finding fails closed next time.
- Re-run the full [owner cutover checklist](./public-repo-owner-cutover-checklist.md)
  before attempting the flip again.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
