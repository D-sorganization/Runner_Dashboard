# Incident Postmortem Template

> Copy this file to `docs/postmortems/YYYY-MM-DD-<short-slug>.md` after the
> incident is resolved. Fill in every section. If a section is genuinely not
> applicable, write `N/A` and explain why.

## Summary

One paragraph: what broke, who was affected, and how long it lasted.

## Impact

- **Severity:** P1 / P2 / P3
- **Start (UTC):** `YYYY-MM-DD HH:MM`
- **Detected (UTC):** `YYYY-MM-DD HH:MM`
- **Mitigated (UTC):** `YYYY-MM-DD HH:MM`
- **Resolved (UTC):** `YYYY-MM-DD HH:MM`
- **Duration:** total / detection-to-mitigation / mitigation-to-resolution
- **User-visible effect:** which dashboard tabs, runners, workflows, or
  agents were degraded or unavailable.
- **Data loss / safety implications:** none / describe.

## Timeline (UTC)

Event-by-event log with timestamps. Include detection signals, paging,
hypotheses considered, commands run, and the moment user impact ended.

```
HH:MM  First alert / symptom observed
HH:MM  Operator paged
HH:MM  Mitigation applied
HH:MM  User impact ends
HH:MM  Root cause identified
HH:MM  Permanent fix deployed
```

## Root Cause

What actually caused the incident. Include the chain of contributing factors,
not just the proximate trigger. Cite commits, config diffs, or upstream
changes where relevant.

## Detection

How was the incident detected? Was the runbook's "Detection" section accurate?
If not, what signal would have caught it earlier?

## Resolution

What was done to fully resolve the incident (not just mitigate). Link the PRs,
commits, or config changes that constitute the permanent fix.

## What Went Well

Concrete things that helped. Tools, runbooks, alerts, or people that worked
as intended.

## What Went Poorly

Concrete things that hurt. Missing alerts, slow detection, broken runbook
steps, unclear ownership, manual toil, etc.

## Action Items

| # | Owner | Due | Description | Tracking issue |
|---|-------|-----|-------------|----------------|
| 1 |       |     |             |                |
| 2 |       |     |             |                |

Each action item must be:

- assigned to a single owner,
- have an explicit due date,
- linked to a tracked issue in `D-sorganization/Runner_Dashboard` (or the
  appropriate sibling repo).

## Runbook Updates

List every runbook in `docs/runbooks/` that should be updated based on this
incident, and what specifically should change. If a new runbook is needed,
add it here and open an issue.

## Related Links

- Incident channel transcript:
- Affected PRs / commits:
- Relevant dashboard screenshots:
- Sibling-repo issues (`Repository_Management`, `Maxwell-Daemon`):
