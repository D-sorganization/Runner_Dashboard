# Owner Cutover Checklist: Going Public

Issue #3085, epic #3077 (slice 7). This is the doc-tracked copy of epic
#3077's own "Owner checklist" section — every item here matches the epic
body exactly (`tests/test_public_repo_cutover_docs.py` checks the two stay
in sync). Every item is an owner-only action; this repository's tooling
computes plans and reports, but never performs any of these steps itself.

Run this only after every landing-order slice (#3079–#3086) is merged and
green.

## Checklist

- [ ] Review the scan reports (tree, history, Actions logs/artifacts, forks,
      issue/PR discussions) and approve.
- [ ] Approve the rotation list and rotate those credentials.
- [ ] Freeze the private repo: confirm zero open PRs and set it read-only.
- [ ] Create the private `Runner_Dashboard-overlay` repo.
- [ ] Rename this repo to `Runner_Dashboard-private` and archive it; create
      the fresh public `Runner_Dashboard` repo and run the bootstrap in
      apply mode; confirm the merge queue, rulesets, required checks and
      re-filed holds, including the HOLD #2657 equivalent.
- [ ] Approve the runner-group scoping for the heavy self-hosted jobs.
- [ ] Place the private overlay on each host and set `RD_OVERLAY_PATH`.
- [ ] Confirm the rollback steps and who runs the recurring scan.
- [ ] Flip visibility to public (Dieter's action only).

## Related

- Scan reports: [`public-repo-secret-scanning.md`](./public-repo-secret-scanning.md).
- Rotation list: [`public-repo-credential-rotation.md`](./public-repo-credential-rotation.md).
- Rollback if something goes wrong after flipping: [`public-repo-rollback.md`](./public-repo-rollback.md).
- Bootstrap (settings-as-code dry-run/apply): `scripts/bootstrap_public_repo.py`.
- Issue migration (sanitized, dry-run by default): `scripts/migrate_issues.py`.
