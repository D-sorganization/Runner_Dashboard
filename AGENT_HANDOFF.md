# Agent Handoff — Runner Dashboard

## GitHub Actions Artifact Transport — #2115

- Branch `codex/2115-artifact-transport`, PR #2125. Remote head is `092318f4`; local follow-up `ae7e9ef1` is unpushed.
- The artifact test client now uses an async pytest fixture and closes clients on its owned loop. Python 3.11 xdist reproduced the missing-loop teardown error before the fix; 69 focused artifact/client/retry tests pass after it.
- Python 3.11 non-slow Linux suite: 7,255 passed, 46 skipped, 1 xfailed. Ruff, format, targeted mypy and detect-secrets baseline comparison pass.
- Existing deployment receipt identifies Dashboard `b604`; this transport change has not been deployed or activated.
- Parent owns protected central pre-PR qualification/publication. The unchanged RD base reports inherited 5 WIP entries against its declared limit of 4; no strict-governance-green claim is made.

## Brick Dashboard Delivery — #2049, #2081 And #2089

- Owner: `codex-brick-20261021-artifact-provenance`; active combined branch
  `codex/2081-artifact-provenance` is based on actual merged #2082 (`d4e3060b`).
- Release caller correction #2082 merged at `d4e3060b`: packaging receives
  `steps.source.outputs.sha`, including existing-tag recovery where the event
  SHA differs. Its YAML/runtime tests, Actionlint and ShellCheck passed.
- This follow-up makes local/CI provenance truthful and binds metadata to the
  initial clean checkout across the build. Preserve release and installer
  compatibility; a context hint is not a signing or attestation proof.
- Focused provenance, artifact and offline-installer tests passed before the
  dependency merge. The metadata input object preserves the CLI contract and function-size budget.
  Combined qualification and follow-up publication remain required.
- #2089 restores existing explicitly enabled loopback GET/HEAD fleet reads.
  Invalid credentials, remote peers and unauthorized writes remain denied.
- This branch incorporates this chat's stopped #2050 source with a normal merge;
  leave redundant-PR closure to the designated closer. Do not mutate peer PRs.
- Keep source delivery separate from the operational artifact/deployment gates;
  issue #2081 closes only after its source criteria are proven through merge.

## Coordinated Dependencies and Evidence

- Existing deployment receipt identifies the Runner Dashboard service at `b604`.
  The artifact transport in PR #2125 is not part of that deployment; its first
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
- The combined branch includes #2049 user/system scope selection and scope-correct
  rollback, plus a missing-token diagnostic correction. Qualify the combined
  source before deployment; user scope must skip unrelated privileged maintenance.
- Repository_Management #2253/#2278 govern admission, trusted dispatch context,
  reservations and reviewed migration. Provider packaging does not activate them.
- No capacity increase, settings/protection change, peer PR/CI mutation,
  fabricated acceptance, or first-user-job dispatch is part of this slice.
