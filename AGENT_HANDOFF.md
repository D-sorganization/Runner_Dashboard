# Agent Handoff — Runner Dashboard

## Release Source Identity — #2081

- Owner: `codex-brick-20261021-artifact-provenance`; workflow-only branch
  `codex/2081-release-source`, based on protected-main `2cfa3e3c`.
- Existing-tag recovery checks out a tag that can differ from the workflow
  event SHA. Packaging must receive the actual checked-out source commit from
  `steps.source.outputs.sha`; normal releases retain the same source identity.
- Workflow/runtime regressions cover normal releases, existing-tag recovery and
  resolver outputs. Actionlint now recognizes the existing fleet label and
  passes without suppressing ShellCheck findings. Behavioral tests are split
  from YAML contracts to keep each test file below 400 lines. Full pre-PR
  remains required for the corrected source before re-arming this own PR.
  Do not publish or deploy from an unqualified source.
- The separate `codex/2081-artifact-provenance` branch tightens artifact metadata
  and source checks after this caller correction merges. Keep workflow changes
  separate from that packaging PR. Issue #2081 remains open until both slices
  satisfy its criteria.

## Operational Boundaries

- This slice does not publish a release, build a production artifact, restart a
  service, alter credentials, change capacity/settings, or invoke maintenance.
- Brick currently uses an existing healthy user service at deployed `a2f47789`.
  The first dashboard dry run and real job remain reserved for the user.
- User-service work under #2049 is separate. The system-service update script
  must not be applied unchecked to Brick's user service.
- Repository_Management #2253/#2278 admission, reservations, record migration
  and reconciliation remain separate open work. API health does not prove
  production browser OAuth readiness.
