You are running a PREMORTEM on the target repositories listed below.

A premortem (Gary Klein, HBR 2007) inverts the usual risk review: instead of
asking "what might go wrong?", assume the project has ALREADY FAILED and explain
why. Imagining a certain failure surfaces risks that optimism and sunk cost
otherwise hide.

## Inputs (fill in before dispatch)

- Target repositories: <owner/repo, owner/repo, ...>
- Shared modules / folders between them: <e.g. vendor/<submodule>, src/shared/python/<pkg>, data repo>
- Failure horizon: <e.g. 12 months from today>
- Definition of "failed": <e.g. abandoned, results untrusted, cannot build, cannot onboard a second contributor>
- Output path: <e.g. docs/assessments/premortem/YYYY-MM-DD-premortem.md>

## Rules

- READ-ONLY on the target repositories. Do not edit source, push, or file
  issues; write only the report (and the draft issues file) at the output path.
- Measure, do not guess. Every finding cites file paths with line numbers and
  numbers you actually measured (counts, sizes, ages, ratios). Label anything
  inferred as "(inferred)".
- Never copy private or personal data into the report. Describe structure,
  counts and governance only.
- Prefer few, high-confidence findings over many speculative ones.

## Procedure

1. **Set the scene.** Write one paragraph, in past tense, dated at the failure
   horizon: "It is <date>. <Project> has failed: <definition>."
2. **Map the system.** List each repository, what it owns, and every sharing
   mechanism between them (git submodule, vendored copy, symlink, pinned
   manifest, published package, copied file, shared data repo). Draw a short
   text diagram.
3. **Generate failure reasons independently** across these lenses, at least
   one candidate per lens:
   - Scientific / domain validity (results trusted without independent validation)
   - Scope and surface area vs maintainer capacity (bus factor)
   - Code health (file/function size, duplication, suppressed lint, dead code)
   - Test reality (what CI actually executes vs what is skipped or mocked)
   - Dependency, toolchain and build fragility
   - Cross-repo coupling and version skew of shared modules
   - Data, privacy, security and licensing
   - Process, CI cost and governance overhead (incl. AI-agent churn)
   - Documentation and onboarding (can a newcomer learn it?)
4. **Gather evidence** for each candidate. Drop candidates you cannot support.
5. **Score** each surviving finding: Likelihood (H/M/L) x Impact (H/M/L) with a
   one-line justification for each, and compute a priority
   (H/H = P0, H/M or M/H = P1, M/M = P2, else P3).
6. **Find the early warning signal**: a measurable indicator that would show
   this failure is starting, and where to read it.
7. **Propose a mitigation**: concrete, scoped, assignable; name the repo and
   files. Prefer removing things over adding process.
8. **Explain for a newer engineer**: one sentence per finding naming the
   underlying engineering concept and why it matters.
9. **Cluster** findings into 3-6 root-cause themes, and say which single
   change would retire the most risk.

## Report format (markdown)

```
# Premortem: <scope> (<date>)
## 1. Scenario
## 2. System Map (repos, shared modules, sharing mechanisms, diagram)
## 3. Metrics Snapshot (table of raw measured numbers)
## 4. Findings
### <ID> <Title>  [P0..P3, Likelihood x Impact]
- Failure story (past tense):
- Evidence:
- Likelihood / Impact:
- Early warning signal:
- Mitigation:
- Beginner note:
## 5. Root-Cause Themes
## 6. Risk Register (table: ID, title, L, I, priority, owner repo, signal)
## 7. Recommendations (ordered, top 5)
## 8. Method Notes and Limits (what was not measured, and why)
```

## Draft issues

Also write `<output path stem>-draft-issues.md`: one draft GitHub issue per
P0/P1 finding (and grouped P2s), each with Title, target repo, labels,
Problem, Evidence, Proposed change, Acceptance criteria (checkboxes, testable),
and Links to the finding ID. Do NOT file them; a human reviews first.
