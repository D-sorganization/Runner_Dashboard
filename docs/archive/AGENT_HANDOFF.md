# Agent Handoff — Runner Dashboard

> Archived session notes (moved from repo root). Canonical operator handoff is
> `docs/development/HANDOFF.md` (collated from PR change fragments).

## GitHub Actions Artifact Transport — #2115

- PR #2125, branch `codex/2115-artifact-transport`: bounded transport and async fixture correction are qualified locally; follow required source and merge-group CI.
- The fixture closes clients on the pytest-owned loop. Its missing-global-loop regression failed before the correction; 69 focused tests pass on Python 3.11 and 3.12.
- Python 3.11 non-slow Linux suite: 7,255 passed, 46 skipped, 1 xfailed. Ruff, formatting, targeted mypy and the unchanged detect-secrets baseline comparison pass.
- All eight local pre-PR gates passed on the corrected code. Semgrep is advisory/unavailable; absent consumer fleet-policy/development-log providers are explicitly skipped.
- A separate protected canonical check still rejects inherited five in-progress entries against the declared limit of four and historical verification fields. This source does not establish strict-governance adoption.
- The original nine-run source cohort is terminal. Only test-fixture teardown and the synthetic credential URL baseline check failed; a single qualified follow-up replaces that source.

## Brick Dashboard Deployment

- Deployed baseline: merged PR #2086, source `b604bd4081d51c78df90144cdb086aace65a2cc1`, Dashboard 4.10.0. The artifact transport above is not deployed or activated.
- Qualified artifact SHA-256: `340ae8cdd6329f664852335723223930eb11446a63a91985fe3854b26b7ae571`. The reviewed user-scope installer preserved rollback at `dashboard.bak.20261021_152852`.
- Read-only health on 2026-10-22 reported healthy, GitHub App authentication OK and GitHub API connected. Earlier settled Queue/Fleet browser verification populated data without JavaScript errors; API health does not establish production OAuth readiness.
- Preserve user/system scope selection, truthful source provenance and scoped local fleet-read authorization from that deployed baseline. No new settings, credentials, flags, services or runner capacity changed in this transport slice.

## Governance Dependencies

- Repository_Management #2299 merged at `5c573e3804494b47bf71c639107ae80f2cd4d6da`: private provider App read access and isolated publisher concurrency are implemented.
- UpstreamDrift #13618 owns the governed public-consumer local workflow copy and natural CI report publication. Public callers cannot directly call the internal provider's reusable workflow.
- Report/receipt provenance consumption, trusted dispatch classification, atomic operation-backed reservations and historical reconciliation remain separate acceptance gates under Repository_Management #2253/#2278.

## Operational Boundaries

- The user's first Dashboard dry run and real job remain reserved. Do not dispatch either during validation.
- Before another deployment, qualify the exact merged source and checksum-bound artifact, preserve rollback and verify the existing user service live.
- Do not alter capacity, repository protection/settings, credentials or peer PR/CI state; do not fabricate acceptance or historical completion dates.
- Leave redundant PR #2050 closure to its designated closer. History remains in Git; this handoff records current state only.
