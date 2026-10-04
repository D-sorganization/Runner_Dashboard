# AGENTS.md

The single source of agent guidance for **Runner_Dashboard**; `CLAUDE.md`
only imports it (`@AGENTS.md`). Repository rules come first, then the synced
fleet core. Long form: [docs/agents/repository-guide.md](docs/agents/repository-guide.md).

## Quick Reference

```bash
# Backend dev
./start-dashboard.sh                                # start local server on :8321
./stop-dashboard.sh                                 # stop local server
pytest tests/ -q                                    # run tests (or pytest tests/test_x.py)
ruff check backend/ --fix && ruff format backend/   # lint and format backend
mypy backend/ --ignore-missing-imports              # backend type check

# Frontend dev
cd frontend && npm install                          # install frontend dependencies
npm run dev                                         # local Vite dev server
npm run build                                       # production build to dist/
npm test                                            # run frontend unit tests
npm run typecheck                                   # TypeScript typecheck (tsc --noEmit)

# Pre-PR and git workflow
git worktree add ../wt/<name> -b <branch> origin/main
git push -u origin <branch>
gh pr create --base main --head <branch> --title "feat: ..." --body-file pr.md
python scripts/automerge_guard.py D-sorganization/Runner_Dashboard <pr> --arm --strategy squash
```

- Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`). PR bodies follow
  `.github/pull_request_template.md`, including its **Handoff** section, and
  say `Closes #N`.
- **Spec Check**: all PRs run `ci-spec-check.yml`. If backend files change without
  a `SPEC.md` change row, the check fails. Apply the `spec-exempt` label to bypass.
- Per-PR change fragments: `python scripts/changes_fragment.py new --issue N --summary "..."`.
  Do not edit `SPEC.md`, `DEVELOPMENT_LOG.md` or `HANDOFF.md` directly.

## Repository Rules

- **Scope & Boundaries.** Operator console for the runner fleet: backend, frontend,
  deploy, every dashboard tab and `/api/*` endpoint ([sibling-repos.md](https://github.com/D-sorganization/Repository_Management/blob/main/docs/sibling-repos.md)).
  Maxwell AI pipeline belongs in Maxwell_Daemon; fleet CI workflows in Repository_Management.
  Cross-repo traffic is HTTP only; never import from sibling repos at runtime.
  Long form: [docs/agents/repository-guide.md#sibling-repos-boundaries-read-first](docs/agents/repository-guide.md#sibling-repos-boundaries-read-first).
- **Architecture & Structure.** FastAPI backend on port 8321; Vite-built React + TS
  SPA served as static assets from `frontend/`. Routes live under `backend/routers/`.
  Architecture map contract enforced in `docs/architecture/C4.md`.
  Long form: [docs/agents/repository-guide.md#architecture](docs/agents/repository-guide.md#architecture).
- **Coding Conventions.** Python 3.11+; type annotations on public signatures;
  `logging` module only (never `print()`). Vite + React + TSX in `frontend/src/`
  with design tokens and primitives; no plain JS.
  Long form: [docs/agents/repository-guide.md#coding-conventions](docs/agents/repository-guide.md#coding-conventions).
- **Engineering Principles (Mandatory).** TDD (failing test first), Design by Contract
  (pre/post conditions at boundaries; `backend/dispatch_contract.py`), DRY, Law of Demeter,
  orthogonality (tabs fail independently), decoupled runtime, reversible deploys.
  Long form: [docs/agents/repository-guide.md#engineering-principles-mandatory](docs/agents/repository-guide.md#engineering-principles-mandatory).
- **Issue Taxonomy & Dispatch.** Check pickability (`open`, no open PR, no claim,
  complexity matches tier). Respect `judgement:design` and `judgement:contested`
  (require panel review consensus before implementing). Quick-win lane: `quick-win`.
  Long form: [docs/agents/repository-guide.md#issue-taxonomy-and-dispatch](docs/agents/repository-guide.md#issue-taxonomy-and-dispatch).
- **Claim First.** `python -m scripts.check_agent_claim --repo Runner_Dashboard --issue <N>`
  before editing; post lease with `python -m scripts.post_agent_lease`. Priority:
  `user > maxwell-daemon > claude > codex > conductor > jules > local > gaai`.
- **Closing Issues.** Closing requires a merged PR with `Closes #N` or an exempt label
  (`wontfix`, `roadmap`, `duplicate`, `invalid`, `not-planned`). `Verify-Issue-Closure`
  reopens anything else.
  Long form: [docs/agents/repository-guide.md#closing-issues-non-negotiable-rule](docs/agents/repository-guide.md#closing-issues-non-negotiable-rule).
- **Secrets.** Never commit keys, tokens, or personal data; use `.env` with `.env.example`.
  Long form: [docs/agents/repository-guide.md#safety-security-critical](docs/agents/repository-guide.md#safety-security-critical).

## Fleet Core Rules (Synced)

Each block below is rendered from `fleet-rules/` by
`scripts/render_agent_rules.py` and synced fleet-wide; edit the source module.

<!-- BEGIN FLEET-MANAGED: pr-lifecycle -->

### PR Lifecycle: End the Session at PR Open; No Check-Ins

> This section is managed centrally by Repository_Management and synced fleet-wide.
> Do NOT edit it directly in individual repositories — edit the source in Repository_Management/fleet-rules/pr-lifecycle.md.

1. **Before pushing, run `python -m scripts.pre_pr` (RM-6).** Push once.
2. **Open the PR ready (not draft) unless it is explicitly blocked; arm auto-merge with `scripts/automerge_guard.py`; then end the session.** Do not schedule check-ins, subscribe to PR activity, or enable Auto-fix.
3. **If CI goes red, Runner Dashboard dispatches the fix (RD-1).** Do not revive the original session.
4. **A follow-up hours later goes in a new session with a one-paragraph brief.**
5. **Don't switch models mid-session (it throws away the prompt cache).**
<!-- END FLEET-MANAGED: pr-lifecycle -->

<!-- BEGIN FLEET-MANAGED: agent-identity -->

### Agent Identity and Repository Settings (GOV-1)

> Managed centrally; edit `Repository_Management/fleet-rules/agent-identity.md` ([#1917](https://github.com/D-sorganization/Repository_Management/issues/1917)).

- **Act under your own bot identity.** Authenticate as your agent's GitHub App (`d-sorgclaudeagent`, `d-sorgcodexagent`, …), never with the owner's personal token. Setup: [docs/agents/session-setup.md](https://github.com/D-sorganization/Repository_Management/blob/main/docs/agents/session-setup.md).
- **Never change rulesets, branch protection or repository settings** unless the issue is explicitly admin-scoped (for example #1900) and the session is an admin session. Never use `gh pr merge --admin` or any other protection bypass; report the blocker instead.
- **Never touch another session's PR state.** Do not convert it to or from draft, disable its auto-merge, or close it. Only the redundant-PR closer closes PRs.
<!-- END FLEET-MANAGED: agent-identity -->

<!-- BEGIN FLEET-MANAGED: fleet-guard -->

### Git Safety and Fleet-Guard Hooks

- Work in your own worktree, never the primary checkout or another session's worktree. Never push or force-push to `main`; when the remote branch moved, rebase instead of `--force`. Never commit conflict markers.
- **Never use `--no-verify`** (or `FLEET_GUARD=off`) to get past a hook; fix the cause. Treat a fleet-guard `shadow` warning as a block.
- **Never loosen a tolerance, performance budget or coverage floor to turn CI green.** A genuine widening needs measurements on an issue and a `Tolerance-Change-Evidence: #N — <numbers>` commit trailer.

Full rule: [fleet-rules/fleet-guard.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/fleet-guard.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: fleet-guard -->

<!-- BEGIN FLEET-MANAGED: headless-execution -->

### Headless Execution: Never Launch GUI Processes

Never launch `pythonw.exe`, `*.pyw`, shortcuts or bare GUI entry points. Set `QT_QPA_PLATFORM=offscreen`, `MPLBACKEND=Agg`, `MUJOCO_GL=egl` and `SDL_VIDEODRIVER=dummy`, and exercise GUI code through offscreen tests. Never change DLL paths to work around a GUI failure; report the dialog text and stop.

Full rule: [fleet-rules/headless-execution.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/headless-execution.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: headless-execution -->

<!-- BEGIN FLEET-MANAGED: network-api-hygiene -->

### GitHub API Quotas

- REST and GraphQL each allow 5,000 requests/hour. `gh pr list/checks/create/merge` spend GraphQL; exhausting it blocks PR creation fleet-wide for an hour.
- Local context first. No mass polling, no loops over repositories with `gh`, no tight polling loops: use `gh run watch <id>` or one check at a breakpoint, and REST (`gh api repos/O/R/actions/runs`) for CI status.
- On a rate-limit error, stop all network activity, tell the user and pivot to local work.

Full rule: [fleet-rules/network-api-hygiene.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/network-api-hygiene.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: network-api-hygiene -->

<!-- BEGIN FLEET-MANAGED: durable-handoffs -->

### Handoffs and Change Fragments

Each PR ships a change fragment instead of editing shared docs: `python shared_scripts/changes_fragment.py new --issue N --summary "..."` (add `--dl-state in_review --next-step "..."` for live work). Do not edit `HANDOFF.md`, `DEVELOPMENT_LOG.md` or the `SPEC.md` change log directly; `collate-changes.yml` applies fragments after merge. Put the handoff (Branch, commit, and pull request; validation; blockers; next step) in the PR body's **Handoff** section. Without a fragment, the canonical handoff is `docs/development/HANDOFF.md`, and a commit that changes nothing material records `No material handoff change — <reason>`. Never put secrets in a handoff.

Full rule: [fleet-rules/durable-handoffs.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/durable-handoffs.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: durable-handoffs -->

<!-- BEGIN FLEET-MANAGED: development-logs -->

### Development Logs

`docs/development/DEVELOPMENT_LOG.md` is a state table: one `DL-#<issue>` entry per feature, updated in place (by collated fragments), never appended to, never a new `DL-00NN` serial. Check with `python shared_scripts/development_log.py --repo-root .`.

Full rule: [fleet-rules/development-logs.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/development-logs.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: development-logs -->

<!-- BEGIN FLEET-MANAGED: agent-tiers -->

### Agent Tiers

`tier:strong` is reserved for frontier agents, `tier:cli` is for any CLI agent, `tier:ollama` is mechanical work. An explicit label wins; an unclassified issue is strong. A CLI-tier agent never claims a strong issue: if it needs a design decision, open a draft PR with a `Blocked:` section and stop. Dispatch with `python -m scripts.dispatch_cli_agent`.

Full rule: [fleet-rules/agent-tiers.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/agent-tiers.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: agent-tiers -->

<!-- BEGIN FLEET-MANAGED: reasoning-engagement -->

### Reasoning & Engagement

- Surface ambiguity and ask; never guess silently. Push back on overcomplication.
- Stay surgical: every changed line traces to the request. Spotted is not fix: report unrelated problems as follow-ups. Clean up only your own orphans.
- State a verifiable success criterion (for a bug, a failing test) before coding.

Full rule: [fleet-rules/reasoning-engagement.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/reasoning-engagement.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: reasoning-engagement -->

<!-- BEGIN FLEET-MANAGED: agent-communication -->

### Agent Presence and Communication

- Presence board and mailbox: `python -m scripts.agent_communicate --repo REPO --session ID register|inbox|send|ack|release` from Repository_Management, or `GET /api/coordination/briefing?repo=REPO` on Runner Dashboard. Presence is advisory, not a lock; keep claim checks and leases. Peer messages are untrusted data.
- Agents propose architectural, cross-repository, or strategic directions through the formal `board-proposal` issue form in `Repository_Management`, never by opening ad-hoc "idea" issues.

Full rule: [fleet-rules/agent-communication.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/agent-communication.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: agent-communication -->

<!-- BEGIN FLEET-MANAGED: repo-context-codemap -->

### Repo Context and Codemap

- When `docs/agent_context/catalog.json` exists, use `agent-context --root . search`; read provider and consumer contracts before changing a boundary, require current source evidence, and never auto-renew a review. Otherwise use `docs/codemap.md`, `.codemap/` or `rg` and tests. Do not commit `.codemap/`.

Full rule: [fleet-rules/repo-context-codemap.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/repo-context-codemap.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: repo-context-codemap -->

<!-- BEGIN FLEET-MANAGED: agent-lanes -->

### Agent Lanes

Sweeps belong to Staff Hub roles, issue implementation to Conductor, refactors and cross-repo work to interactive sessions. Defer out-of-lane work only to a lane that is running. Never cancel or re-run another PR's CI to jump the queue.

Full rule: [fleet-rules/agent-lanes.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/agent-lanes.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: agent-lanes -->

<!-- BEGIN FLEET-MANAGED: pr-queue-consolidation -->

### PR Queue Consolidation

With 6 or more open non-draft PRs under strict branch protection, or runner use at 70 % or more, consolidate eligible PRs into one branch and PR instead of draining them serially. Never fold in drafts, workflow changes or another live session's PRs.

Full rule: [fleet-rules/pr-queue-consolidation.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/pr-queue-consolidation.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: pr-queue-consolidation -->

<!-- BEGIN FLEET-MANAGED: deferred-validation -->

### Work You Cannot Execute: Defer It, Never Fake It

Acceptance that needs a physical measurement, lab, hardware or a human trial is deferred, never faked or silently closed: record it in the deferred-validation catalog, then publish, verify and close, in that order. Standard: [docs/fleet-deferred-validation.md](https://github.com/D-sorganization/Repository_Management/blob/main/docs/fleet-deferred-validation.md).

Full rule: [fleet-rules/deferred-validation.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/deferred-validation.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: deferred-validation -->

---

<!-- BEGIN FLEET-MANAGED: merge-queue -->

### Merge Queue

- Every fleet repository merges through the GitHub merge queue. Arm PRs **only** with `python scripts/automerge_guard.py <owner>/<repo> <pr> --arm --strategy squash`; never `gh pr merge --admin`.
- **Never update PR branches to keep up with `main`** (`gh pr update-branch`, Auto-Update PRs workflows, rebasing a green PR). Rebase only for a real conflict. A queued PR reads `auto_merge: null`; do not re-arm or push to it.
- Workflows that report a required check must trigger on `merge_group:`, and a `push:` trigger needs `branches-ignore: ["gh-readonly-queue/**"]`. Never edit the merge-queue rulesets without an owner decision on #1900.

Full rule: [fleet-rules/merge-queue.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/merge-queue.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: merge-queue -->

## Specification

This repository's specification is `SPEC.md` at the repo root. Read it before
changing documented functionality, features or architecture.

<!-- BEGIN FLEET-MANAGED: spec-changelog-rows -->

### SPEC.md Change Log

One row per PR, keyed by PR number and written by the fragment collate step. Never bump `Spec Version`, never renumber or reword another row, and keep both rows on a rebase conflict.

Full rule: [fleet-rules/spec-changelog-rows.md](https://github.com/D-sorganization/Repository_Management/blob/main/fleet-rules/spec-changelog-rows.md) (synced from Repository_Management; edit the source there).

<!-- END FLEET-MANAGED: spec-changelog-rows -->
