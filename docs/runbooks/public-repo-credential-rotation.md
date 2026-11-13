# Runbook: Public Repo Credential Rotation

Issue #3085, epic #3077 (slice 7). Driven by the **rotation list** at the
bottom of the slice-3 secret-exposure report
(`scripts/scan_history.py --report <private path>`, see
[`public-repo-secret-scanning.md`](./public-repo-secret-scanning.md)). The
report lives outside the repository in a private directory; this runbook
never reproduces its contents — only confirmed credential **categories**
and rotation status belong on the rotation list, never credential values.

This runbook names owner-only steps explicitly. Nothing here runs
automatically against GitHub visibility or rotates a credential by itself.

## Symptom

- The slice-3 report's rotation list has entries still marked
  not-yet-rotated ahead of the cutover's visibility flip.
- A post-cutover scan ([`public-repo-rollback.md`](./public-repo-rollback.md))
  finds a credential category missed before the first flip.

## Severity

**P1** before the public flip — every rotation-list entry must be rotated
before the owner approves cutover (epic #3077's own checklist: "Approve the
rotation list and rotate those credentials").

## Diagnosis

```bash
# Open the private report and read only the "## Rotation list" section.
# Never paste its contents into an issue, PR, commit, or log.
less "$HOME/private/rd-secret-inventory/initial.md"

# For each entry, confirm which system/env file/secret store holds it today.
gh secret list --repo D-sorganization/Runner_Dashboard
ls -la ~/.config/runner-dashboard/env
```

## Mitigation: walk the rotation list, one entry at a time

For every entry on the rotation list:

1. **Revoke the credential at its source** (PAT, fine-grained token, GitHub
   App installation token, VAPID key — see
   [`secrets-compromise.md`](./secrets-compromise.md) for the per-type
   revoke/replace steps; this runbook only adds the "driven by a list, not
   a single incident" loop around them).
2. **Rotate and redeploy** the replacement following those same steps.
3. **Mark the entry rotated** in the private report (edit the local file;
   it stays outside the repo, never committed).
4. **Record evidence** (timestamp, who ran it) next to the entry —
   categories and status only, never the old or new value.

Do not approve cutover, and do not flip the repo to public, until every
entry is marked rotated.

## Resolution

- Once every entry is rotated, the owner checks the matching item on the
  [owner cutover checklist](./public-repo-owner-cutover-checklist.md).
- Schedule a follow-up rotation reminder per credential's natural rotation
  window, same as [`secrets-compromise.md`](./secrets-compromise.md).
- A false-positive match (a placeholder or example value) gets noted in the
  private report, not rotated — but double-check before concluding that.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
