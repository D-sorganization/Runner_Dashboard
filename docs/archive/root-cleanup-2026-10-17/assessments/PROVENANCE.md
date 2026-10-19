# Provenance — `assessments/` root-cleanup archive (2026-10-17)

Archived from the repository root (`assessments/`) by the Sanitation Engineer
pass on 2026-10-17. Nothing outside this directory referenced these files
(verified via a repo-wide grep for the filenames, the generator script names,
and the `assessments/` path).

## What this was

Added whole in PR #168 ("[ASSESSMENT] Pragmatic A-O Fleet Assessment
2026-04-26"): a one-off staging area for a fleet-wide 16-criterion assessment
run, generated and distributed from this repo as the orchestrator.

- `*-assessment.json` / `*-comprehensive-assessment.{json,md}` — per-repo and
  fleet-wide scores for **12 D-sorganization repos**, of which only one
  (`runner-dashboard`) is this repository. The other 11 (`Bitnet_Launcher`,
  `Drake_Models`, `Games`, `MEB_Conversion`, `MLProjects`, `Maxwell-Daemon`,
  `Playground`, `QuatEngine`, `Tools_Private`, `UpstreamDrift`,
  `Worksheet-Workshop`, `controls`) have no other footprint in Runner_Dashboard
  and belong, if anywhere, in their own repos' history or in
  `Repository_Management`.
- `assess_repo.py`, `assess_runner_dashboard.py`, `batch_assess.py`,
  `repo_inventory.py`, `retry_failed.py`, `distribute_assessments.py` —
  one-off generator/distribution scripts for that run. `distribute_assessments.py`
  shows the intended end state: it copies each repo's own assessment into
  that repo's `docs/assessments/` and opens a PR there.
- `open_issues_*.json` — scratch API dumps used while generating the report.

## Why archived, not deleted

The distribution script's own job was already done: Runner_Dashboard's own
copy already lives at `docs/assessments/2026-04-26-runner-dashboard-assessment.json`
(plus the comprehensive report), which is the canonical, intentional location
per the distribution model. The root `assessments/` directory was the
orchestrator's leftover staging copy — superseded for this repo, and foreign
data for the other 11. Per the sanitation playbook's fail-closed rule
("archive dated, don't invent deletions"), it is archived here with
provenance rather than deleted.

## Follow-up (not performed by this pass)

Single-issue, non-bulk follow-up only, per the no-bulk-remediation rule: if an
owner wants this cleaned up further (e.g. the 11 other repos' data purged
entirely), that is a design decision for a dedicated issue, not something
this sanitation pass should decide unilaterally.
