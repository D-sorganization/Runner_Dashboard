# Agent Handoff — Runner Dashboard

## Artifact Build Identity — #2081

- Owner: `codex-brick-20261021-artifact-provenance`; active unpublished branch
  `codex/2081-artifact-provenance` is based on actual merged #2082 (`d4e3060b`).
- Release caller correction #2082 merged at `d4e3060b`: packaging receives
  `steps.source.outputs.sha`, including existing-tag recovery where the event
  SHA differs. Its YAML/runtime tests, Actionlint and ShellCheck passed.
- This follow-up makes local/CI provenance truthful and binds metadata to the
  initial clean checkout across the build. Preserve release and installer
  compatibility; a context hint is not a signing or attestation proof.
- Focused provenance, artifact and offline-installer tests passed before the
  dependency merge. The metadata input object preserves the CLI contract and function-size budget.
  Combined qualification and publication remain required.
- Keep source delivery separate from the operational artifact/deployment gates;
  issue #2081 closes only after its source criteria are proven through merge.

## Coordinated Dependencies and Evidence

- Brick currently runs a healthy existing user service at deployed `a2f47789`.
  The token deployment chat reports no active deployment or hold. Its first
  dashboard dry run and real job remain reserved for the user.
- Merged #2057 supplies agent token access. Revision `875d355d` had passing
  frontend/quality tests; its collate job failed before checkout because the
  App client ID was unavailable. That did not establish a fragment error.
- The earlier `2cfa3e3c` Operations runner-hours frontend failure is outside
  this packaging scope; #2082's source frontend cohort passed.
- Current-consumer pre-PR reports absent fleet-policy/development-log providers.
  A stronger #2253 provider probe rejected inherited verification placeholders
  and missing SHAs. Do not claim new-governance adoption or erase that debt.

## Operational Boundaries and Remaining Gates

- Before deployment: qualify exact source, build a checksum-bound artifact with
  truthful provenance, preserve rollback, and use the existing user service with
  live health/version checks. API health does not prove browser OAuth readiness.
- User-service work under #2049 is separate. The system-service update script
  must not be applied unchecked to Brick or invoke unrelated maintenance here.
- Repository_Management #2253/#2278 govern admission, trusted dispatch context,
  reservations and reviewed migration. Provider packaging does not activate them.
- No capacity increase, settings/protection change, peer PR/CI mutation,
  fabricated acceptance, or first-user-job dispatch is part of this slice.
