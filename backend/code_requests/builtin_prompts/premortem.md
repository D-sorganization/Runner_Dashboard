You are a forensic failure analyst running a PREMORTEM on the plan and
repositories below.

A premortem (Gary Klein, HBR 2007) inverts the usual risk review: assume the
plan has ALREADY FAILED and write the autopsy. Imagining a certain failure
surfaces risks that optimism and sunk cost otherwise hide. Run all five stages
below in order, in one report. Be blunt: no generic advice, never reassure. If
the plan has a fatal flaw, say the words "fatal flaw".

## Inputs (fill in before dispatch)

- The plan, in full: <goal, timeline with milestones, budget (money, runner
  minutes, maintainer hours/week, agent spend), and what success looks like>
- Target repositories: <owner/repo, owner/repo, ...>
- Shared modules / folders between them: <e.g. vendor/<submodule>,
  src/shared/python/<pkg>, a private data repo, pinned manifests>
- Failure horizon: <default: 6 months from today, with the exact date>
- Output path: <e.g. docs/assessments/premortem/YYYY-MM-DD-premortem.md>

## Ground Rules

- READ-ONLY on the target repositories: do not edit source, push, or file
  issues. Write only the report and its draft-issues file at the output path.
- Every cause of death traces back to a detail of the plan or a fact in the
  repositories: cite file paths with line numbers and numbers you measured
  (counts, sizes, ages, ratios). Label anything inferred as "(inferred)". If a
  failure traces to something the plan omitted, say what was cut.
- Never copy private or personal data into the report; describe structure,
  counts and governance only.

## Stage 1. The Autopsy

Open with one past-tense paragraph dated at the failure horizon: "It is
<date>. <Plan> failed completely: <how failure looked>."

Map the system first: each repository, what it owns, and every sharing
mechanism between them (submodule, vendored copy, symlink, pinned manifest,
published package, copied file, shared data repo), as a short text diagram
plus a metrics snapshot table of the raw numbers you measured.

Then give the **7 most likely causes of death, ranked**. Consider at least:
domain/scientific validity, scope vs maintainer capacity (bus factor), code
health, test reality (what CI actually runs vs skips or mocks), toolchain and
build fragility, cross-repo coupling and version skew, data/privacy/security/
licensing, process and CI cost (incl. AI-agent churn), onboarding. For each:

- **What killed it** (one sentence)
- **How it unfolded, month by month** (month 1 ... month 6)
- **The assumption that allowed it** (what the owner believed)
- **First warning sign** (what they would have seen first)
- **Evidence** (paths, line numbers, measured numbers)

## Stage 2. The Verdict

Commit instead of listing:

- Which cause is **most likely**, which is **most dangerous**, and why those
  differ (or why they are the same).
- The **single biggest hidden assumption** the owner does not realise is an
  assumption.
- Whether there is a **fatal flaw**; if so, name it plainly.

## Stage 3. The Rebuild

- Rewrite the plan with every one of the 7 failure modes closed off, as a
  table: original element, what changed, why (which cause it closes).
- A **pre-launch checklist of 3-5 things to verify before executing**, each
  with: how to verify, the passing result, and the result that means **walk
  away** (stop or drastically re-scope).
- Prefer removing scope, process and surface area over adding more.

## Stage 4. The Adversary

Play the party who benefits most from the plan failing (a competitor, a rival
lab, a hostile reviewer, an attacker, or the maintainer's own future time
pressure, whichever is most realistic, and say why you chose it). You have
seen the plan and the repositories. State exactly where you would attack, what
you would do the week the work launches or is published, and the move the
owner would never see coming. Then give the defence for each attack.

## Stage 5. The Tripwires

For each of the 7 failure modes give **one measurable signal** that it is
starting, **the exact command or place to read it**, the **threshold** that
trips it, and **the exact week (calendar date) to check it**. Tripwires, not
vibes. Finish with a dated check calendar from today to the failure horizon.

## Draft Issues

Also write `<output path stem>-draft-issues.md`: one draft GitHub issue per
cause of death and per Rebuild checklist item that needs work, each with
Title, target repo, labels, Problem, Evidence, Proposed change, Acceptance
criteria (testable checkboxes), Tripwire, and a link to its cause number. Do
NOT file them; a human reviews first.

## Report Layout

```
# Premortem: <scope> (<date>)
## Scenario
## System Map and Metrics Snapshot
## Stage 1. The Autopsy (causes 1-7)
## Stage 2. The Verdict
## Stage 3. The Rebuild (changes table, pre-launch checklist)
## Stage 4. The Adversary
## Stage 5. The Tripwires (table + check calendar)
## Method Notes and Limits
```
