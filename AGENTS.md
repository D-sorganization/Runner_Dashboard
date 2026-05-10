# AGENTS.md

## 🤖 Agent Personas & Directives

**Audience:** This document is the authoritative guide for AI agents working in this repository.

## 🗺️ Sibling repos — read first

This repo is part of a three-repo fleet. The full contract is in
[`docs/sibling-repos.md`](docs/sibling-repos.md). Before opening an issue or PR
that touches dashboard tabs, HTTP endpoints, the Maxwell pipeline, or any
cross-repo surface, **route to the right repo**:

- Dashboard backend / frontend / endpoints / tabs → [`runner-dashboard`](https://github.com/D-sorganization/runner-dashboard)
- Maxwell pipeline / Strategist / Crucible / sandbox / BYO-CLI → [`Maxwell-Daemon`](https://github.com/D-sorganization/Maxwell-Daemon)
- Fleet-wide workflows / skills / templates / agent coordination → here

A PR that modifies `runner-dashboard/backend/` or `runner-dashboard/frontend/`
paths _from inside this repo_ is structurally wrong. Close it and re-open in
the standalone repo.

`Claude_Skills/` is third-party material and is excluded from Claude context by
`.claudeignore`. Agents must not depend on that directory as readable
authoritative context for repo decisions, and must not modify it directly.

---

**Core Mission:**

- Write high-quality, maintainable, and secure code.
- Adhere strictly to the project's architectural and stylistic standards.
- Act as a responsible pair programmer, always verifying assumptions and testing changes.

---

<!-- BEGIN FLEET-MANAGED: reasoning-engagement -->

## 🧠 Reasoning & Engagement

> This section is managed centrally by Repository_Management and synced fleet-wide.
> Do NOT edit it directly in individual repositories — edit the source in Repository_Management/AGENTS.md.

These rules govern *how* you engage with a task before and during implementation. They exist because LLM agents tend to pick an interpretation silently, overcomplicate the solution, and edit code they were not asked to touch. Each rule directly counteracts one of those failure modes.

- **Surface ambiguity. Do not guess silently.** If the request has more than one plausible interpretation, list the options and ask before implementing. Picking one and running with it is the single most common cause of rework in this fleet.
- **Push back on overcomplication.** If a simpler approach would satisfy the request, say so before you build the complicated one. Do not implement bloated 1000-line constructions when 100 would do. The senior-engineer test: would they call this overcomplicated? If yes, simplify.
- **Stay surgical.** Every changed line must trace directly to the user's request. Do not "improve" adjacent code, comments, formatting, or imports. Do not refactor things that are not broken. Match existing style even if you would do it differently.
- **Spotted ≠ fix.** If you notice unrelated dead code, latent bugs, or stylistic problems while working, *mention them in the PR body or as a follow-up issue* — do not fix them in the same PR. (The `mcp__ccd_session__spawn_task` tool is the right channel when working interactively.)
- **Clean up only your own orphans.** If your changes leave imports, variables, or functions newly unused, remove them. Do not delete pre-existing dead code unless the task asked for it.
- **State a verifiable success criterion before coding.** For a bug fix, that's a failing test that reproduces it (RED → GREEN, see TDD section below). For a feature, the explicit check that says "done." "Make it work" is not a success criterion.

**The diff test:** every line in your final diff should answer "this is here because the user asked for X." If you cannot answer that for a given line, remove it.

<!-- END FLEET-MANAGED: reasoning-engagement -->

---

## 🧭 Fleet Coordination (mandatory before any PR)

This repository is part of a multi-agent fleet. Before starting work on any issue you must:

- **Check the claim.** `python -m scripts.check_agent_claim --repo <repo> --issue <N>`. If the JSON output has `"held": true`, another agent (or human) already owns this issue — pick a different one.
- **Post a lease.** `python -m scripts.post_agent_lease --agent <your-id> --session <session-id> --repo <repo> --issue <N>`. Default TTL is 2 hours; the reaper sweeps expired leases.
- **Reference the issue in your PR body.** Use `Fixes #N`, `Closes #N`, or `Resolves #N` so the redundant-PR closer can detect duplicates.

**Roster + priority** (higher wins redundancy races):

```
user > maxwell-daemon > claude > codex > jules > local > gaai
```

Label conventions:

- `claim:<agent>` — live claim on an issue (paired with a lease comment).
- `agent:<agent>` — attribution tag on PRs; indicates who authored.
- `do-not-automate` — humans only; no agent may touch.

Reference docs:

- [`docs/agent-coordination-strategy.md`](docs/agent-coordination-strategy.md) — the policy.
- [`docs/agent-lease-protocol.md`](docs/agent-lease-protocol.md) — the contract.
- [`docs/fleet-in-flight.md`](docs/fleet-in-flight.md) — auto-generated status board.

**Fail-open by design.** Every coordination script exits 0 even on internal error; callers proceed and risk duplicate work rather than halt. The redundant-PR closer is the eventual-consistency backstop.

---

## 🛡️ Safety & Security (CRITICAL)

1. **Secrets Management**:
   - **NEVER** commit API keys, passwords, tokens, or database connection strings.
   - Use `.env` files and `python-dotenv` for secrets.
   - Create `.env.example` templates for required environment variables.
2. **Code Review**:
   - Review all generated code for security vulnerabilities (SQL injection, unsafe file I/O, etc.).
   - Do not accept code you do not understand.
3. **Data Protection**:
   - Do not commit large binary files (>50MB) or personal data.

---

<!-- BEGIN FLEET-MANAGED: network-api-hygiene -->

## 🛑 NETWORK & API HYGIENE (CRITICAL)

> This section is managed centrally by Repository_Management and synced fleet-wide.
> Do NOT edit it directly in individual repositories — edit the source in Repository_Management/AGENTS.md.

### GitHub API Quotas

| API Type                  | Quota        | Consumed By                                                        |
| ------------------------- | ------------ | ------------------------------------------------------------------ |
| REST (`gh api repos/...`) | 5,000 req/hr | Safe for polling                                                   |
| GraphQL                   | 5,000 req/hr | `gh pr list --json`, `gh pr checks`, `gh pr create`, `gh pr merge` |

GraphQL and REST have **separate** quotas. Exhausting GraphQL blocks PR creation and merging fleet-wide for an entire hour.

### Mandatory Rules

- **NO MASS POLLING**: Agents MUST NEVER use `gh pr list`, `gh issue list`, or arbitrary REST/GraphQL loops in a bulk manner to "scan" or "sweep" the repository fleet. Single, scoped repository lookups are allowed when needed (e.g., checking if a specific PR exists).
- **LOCAL FIRST**: Rely on local `.md` files, previously generated `issues.json` artifacts, or user assistance to find task context — do not query GitHub to discover what to work on.
- **NO PARALLELIZED GITHUB CLI**: Never write or execute scripts that loop over multiple repositories performing `gh` operations (automated PR merge scripts, fleet-wide status sweeps, etc.).
- **NO TIGHT POLLING LOOPS**: Never implement `while true; do gh pr checks $PR; sleep 30; done` patterns. Each iteration of such a loop costs 1–3 GraphQL calls; at 30-second intervals that drains the 5,000/hr quota in under 3 hours.
  - ❌ `while true; do gh pr checks; sleep 30; done`
  - ✅ `gh run watch <run-id>` — streams CI events without polling
  - ✅ Check status once at natural work breakpoints (after completing other tasks)
- **BATCHING**: If remote information is absolutely necessary, use a single focused query — not a loop of queries.
- **REST OVER GRAPHQL FOR CI STATUS**: Use REST endpoints for CI polling; they don't consume the GraphQL quota.
  - ❌ `gh pr checks <N>` (GraphQL)
  - ✅ `gh api repos/OWNER/REPO/actions/runs` (REST)
  - ✅ `gh api repos/OWNER/REPO/actions/jobs/<id>/logs` (REST)
- **STOP MONITORS IMMEDIATELY**: When using background monitor tasks, call `TaskStop <id>` the moment the monitored condition is satisfied. Do not leave monitors running "just in case."
- **LONG POLLING INTERVALS**: Background monitors must use ≥270-second intervals (keeps the prompt cache warm). Default to 1200–1800 s for idle monitoring. Never chain short sleeps to work around the 60-second minimum.
- **SILENT FAILURES**: If an API rate limit is hit, HALT NETWORK ACTIVITY IMMEDIATELY. Do not write retry-loops that further exhaust the quota. Alert the user and pivot to local work.

### Checking Rate Limit Status

```bash
gh api rate_limit | python3 -c "
import json, sys, datetime
d = json.load(sys.stdin)['resources']
for k in ['core', 'graphql']:
    r = d[k]
    reset = datetime.datetime.fromtimestamp(r['reset']).strftime('%H:%M:%S')
    print(f'{k}: {r["remaining"]}/{r["limit"]} remaining — resets {reset}')
"
```

<!-- END FLEET-MANAGED: network-api-hygiene -->

---

## 🐍 Python Coding Standards

### 1. Code Quality & Style

- **Logging vs. Print**:
  - ❌ **DO NOT** use `print()` statements for application output.
  - ✅ **USE** the `logging` module.
  - _Example_: `logger.info("Processing complete")` instead of `print("Processing complete")`.
- **Imports**:
  - ❌ **NO** wildcard imports (`from module import *`).
  - ✅ **Explicitly** import required classes/functions.
- **Exception Handling**:
  - ❌ **NO** bare `except:` clauses.
  - ✅ **Catch specific exceptions** (e.g., `except ValueError:`) or at least `except Exception:`.
- **Type Hinting**:
  - Use Python type hints for function arguments and return values.

### 2. Project Structure

```
project_name/
├── README.md
├── requirements.txt
├── .gitignore
├── .env.example
├── src/
│   └── project_name/
│       ├── __init__.py
│       └── main.py
└── tests/
```

### 3. Testing

- Use `unittest` or `pytest`.
- Write unit tests for individual functions and integration tests for workflows.

### 4. Test-Driven Development (TDD) - RED, GREEN, REFACTOR

**MANDATORY**: All new code must follow the Test-Driven Development methodology:

1. **🔴 RED - Write a Failing Test First**

   - Before writing any production code, write a unit test that defines the new functionality or behavior.
   - The test MUST fail initially because the production code has not yet been written.
   - This ensures you understand the requirements before implementation.

2. **🟢 GREEN - Make the Test Pass**

   - Write the **minimal** amount of production code necessary to make the failing test pass.
   - The goal is purely to pass the test, not to write perfect or optimized code.
   - Resist the temptation to add features not covered by tests.

3. **🔵 REFACTOR - Clean Up the Code**
   - Once the test passes, clean up the newly written code:
     - Remove duplication
     - Rename variables for clarity
     - Extract functions/methods
     - Improve structure
   - Ensure all existing tests continue to pass after refactoring.
   - This step prevents "technical debt" from accumulating.

**Benefits of TDD:**

- Forces clear thinking about requirements before implementation
- Produces comprehensive test coverage as a byproduct
- Results in modular, testable code by design
- Catches bugs early when they're cheapest to fix

**Example Workflow:**

```python
# 1. RED: Write failing test
def test_calculate_distance():
    result = calculate_distance(0, 0, 3, 4)
    assert result == 5.0  # Test fails - function doesn't exist

# 2. GREEN: Write minimal code to pass
def calculate_distance(x1, y1, x2, y2):
    return ((x2-x1)**2 + (y2-y1)**2) ** 0.5  # Test passes

# 3. REFACTOR: Improve code quality
import math

def calculate_distance(x1: float, y1: float, x2: float, y2: float) -> float:
    """Calculate Euclidean distance between two points."""
    return math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
```

### 5. Code Design Principles (MANDATORY)

All code produced must adhere to the following design principles. These are evaluated during periodic assessments (see `docs/assessments/`).

#### 5a. DRY — Don't Repeat Yourself

- ❌ **DO NOT** duplicate logic across modules, functions, or files.
- ✅ **Extract** shared logic into utility functions, base classes, or shared libraries.
- **Threshold:** Any logic block >5 lines appearing in 2+ locations MUST be refactored.

#### 5b. Design by Contract (DbC)

- ✅ **Validate** function inputs at API boundaries with explicit precondition checks.
- ✅ **Document** preconditions, postconditions, and invariants in docstrings.

#### 5c. Orthogonality & Decoupling

- ❌ **DO NOT** create circular imports or tightly coupled modules.
- ✅ **Ensure** changing one module does not require changes in unrelated modules.

#### 5d. No Monolithic Files

- ❌ **DO NOT** create files exceeding **400 lines**. Files >800 lines are critical violations.

#### 5e. Reversibility

- ❌ **DO NOT** hard-code file paths, database endpoints, or API URLs.
- ✅ **Externalize** all configuration to `.env`, config files, or CLI arguments.

#### 5f–5n. Additional Standards

- **Function Length:** Max 50 lines, target ≤20. Max 4 parameters, target ≤3.
- **Law of Demeter:** No chained access >2 levels.
- **No God Functions:** Max 2 responsibilities per function. >80 lines is critical.
- **No Magic Numbers:** Extract to named constants.
- **Name Quality:** Descriptive, snake_case. No single-letter names outside loops.
- **Comment Quality:** Explain WHY not WHAT. Google/NumPy-style docstrings.
- **No Deprecated Code:** Remove sys.path hacks, dead code, stale TODOs.
- **Standard Structure:** Use `src/`, `tests/`, `docs/assessments/`, `docs/development/`.

---

### 6. Calculation & Performance Standards

- ❌ **DO NOT** use Python `for` loops to iterate over NumPy arrays — use vectorized operations.
- ✅ **Precompute** loop-invariant values outside of loops.
- ✅ **Profile** before optimizing — use `cProfile` or `%timeit`.

---

## 🔢 MATLAB Coding Standards

### 1. Structure

```
matlab_project/
├── main.m
├── src/
│   ├── functions/
│   └── classes/
└── tests/
```

### 2. Best Practices

- Use clear comment blocks for function documentation.
- Avoid `.asv` and `.m~` files in commits (add to `.gitignore`).
- Use `functiontests` for testing.

---

## 🔄 Git Workflow & Version Control

### 1. Commit Messages

Use **Conventional Commits** format:

- `feat(scope): description` (New feature)
- `fix(scope): description` (Bug fix)
- `docs(scope): description` (Documentation)
- `style(scope): description` (Formatting)
- `refactor(scope): description` (Code restructuring)
- `test(scope): description` (Adding tests)
- `chore(scope): description` (Maintenance)

### 2. Branching Strategy

- `main`: Production-ready code.
- `develop`: Integration branch.
- `feature/name`: New features.
- `hotfix/name`: Critical bug fixes.

#### Agent / Automation Branch Naming (vendor-neutral)

All automated and agent-generated branches MUST use the following vendor-neutral prefixes.
Do **NOT** use vendor-specific prefixes (`claude/`, `jules/`, `copilot/`).

| Prefix                        | Use case                                                    |
| ----------------------------- | ----------------------------------------------------------- |
| `bot/`                        | All agent-generated branches (Jules, Claude, Copilot, etc.) |
| `auto/`                       | Fully-automated CI/CD branches (no human agent involvement) |
| `fix/issue-XXXX-description`  | Human-initiated issue fixes                                 |
| `feat/issue-XXXX-description` | Human-initiated feature work                                |
| `docs/issue-XXXX-description` | Human-initiated documentation updates                       |

Examples:

- `bot/issue-resolver-20260324-0130`
- `bot/auto-fix-linting-20260324-013045`
- `auto/compiled-quality-2026-03-24`
- `fix/issue-206-branch-naming-convention`

---

## 📝 Documentation

- **README.md**: Every project must have a README with Description, Installation, and Usage sections.
- **Docstrings**: Use Google or NumPy style docstrings for Python.
- **Comments**: Explain _why_, not just _what_.

---

## 🌐 Web Development Standards (HTML/CSS/JS)

### 1. HTML

- **Semantic HTML**: Use `<header>`, `<nav>`, `<main>`, `<footer>`, `<article>`, `<section>` appropriately.
- **Accessibility**: Ensure all `<img>` tags have `alt` attributes. Use ARIA labels where necessary.
- **Structure**: Maintain a clean and indented structure.

### 2. CSS

- **Naming Convention**: Use **BEM** (Block Element Modifier) for class names where possible (e.g., `.card__title--large`).
- **Responsiveness**: Design **Mobile-First**. Use media queries to adapt to larger screens.
- **Linting**: Use `stylelint` with standard config.
  - Avoid ID selectors for styling.
  - Avoid `!important`.

### 3. JavaScript

- **Modern Syntax**: Use ES6+ features (arrow functions, template literals, destructuring).
- **Variables**: Use `const` by default, `let` if reassignment is needed. ❌ **NEVER** use `var`.
- **Async/Await**: Prefer `async/await` over raw Promises/callbacks.
- **Linting**: Use `eslint`.
- **Equality**: Always use strict equality `===` and `!==`.

---

## ⚙️ C++ Coding Standards

### 1. Style Guide

- Follow the **Google C++ Style Guide**.
- **Formatting**: Use `clang-format`.
  - Indent width: 4 spaces (as seen in `.clang-format`).
  - Column limit: 0 (no hard limit, but keep it readable).
  - Brace wrapping: Allman style (braces on new line) is configured in some repos, but consistency within the specific repo is key.

### 2. Modern C++

- Use **C++11/14/17** features.
- **Memory Management**:
  - ❌ **Avoid** raw pointers (`new`/`delete`).
  - ✅ **Use** smart pointers: `std::unique_ptr` for exclusive ownership, `std::shared_ptr` for shared ownership.
- **RAII**: Use Resource Acquisition Is Initialization for resource management.

### 3. Safety

- Avoid C-style casts; use `static_cast`, `dynamic_cast`, etc.
- Initialize all variables upon declaration.

---

## 🚨 Emergency Procedures

If sensitive data is accidentally committed:

1. **Stop** immediately.
2. Use `git filter-branch` or BFG Repo-Cleaner to remove the file from history.
3. Force push only if necessary and coordinated with the team.

---

## 🏗️ System Architecture & Agent Roles

**Reference:** [JULES_ARCHITECTURE.md](JULES_ARCHITECTURE.md)

This section defines the active agents within the Jules "Control Tower" Architecture. All agents must operate within their defined scope.

### Overview: Overnight Automation Schedule (PST)

| Time (PST) | Agent                 | Purpose                                   |
| ---------- | --------------------- | ----------------------------------------- |
| 12:00 AM   | Assessment Generator  | Generate code quality assessment reports  |
| 12:30 AM   | Code Quality Reviewer | Review and fix code quality issues        |
| 1:00 AM    | Completist            | Find and fix incomplete implementations   |
| 1:30 AM    | Documentation Auditor | Update and improve documentation          |
| 2:30 AM    | Sentinel              | Security scanning and vulnerability fixes |
| 3:00 AM    | Auto-Refactor         | Apply DRY/orthogonality improvements      |
| 3:30 AM    | Issue Resolver        | Work on open GitHub issues                |
| 4:00 AM    | PR Compiler           | Consolidate multiple PRs into one         |
| 5:00 AM    | Auto-Rebase           | Rebase PRs onto main, resolve conflicts   |

---

### 1. The Control Tower (Orchestrator)

**Role:** Air Traffic Controller
**Workflow:** `.github/workflows/Jules-Control-Tower.yml`
**Responsibilities:**

- **Orchestrator:** Coordinates specialized agent workflows via scheduled cron jobs and event triggers.
- **Decision Maker:** Analyzes the event context (Triage) and dispatches the appropriate specialized worker.
- **Loop Prevention:** Enforces `if: github.actor != 'jules-bot'` to prevent infinite recursion.
- **Schedule Router:** Routes scheduled jobs to the correct worker based on cron time.

### 2. Assessment Generator (The Auditor)

**Role:** Quality Assessment Reporter
**Workflow:** `.github/workflows/Jules-Assessment-Generator.yml`
**Schedule:** Midnight PST (0 8 ** \* UTC)
**Capabilities:\*\*

- **Read:** Entire codebase for quality analysis
- **Write:** Assessment reports to `docs/assessments/`
- **Constraint:** Read-only for source code; only writes reports.

### 3. Code Quality Reviewer (The Inspector)

**Role:** Code Quality Enforcer
**Workflow:** `.github/workflows/Jules-Code-Quality-Reviewer.yml`
**Schedule:** 12:30 AM PST (30 8 ** \* UTC)
**Capabilities:\*\*

- **Read:** Linting results, type check outputs
- **Write:** Fixes for style, formatting, and minor code issues
- **Constraint:** Limited to auto-fixable issues (ruff).

### 4. Completist (The Finisher)

**Role:** Incomplete Implementation Hunter
**Workflow:** `.github/workflows/Jules-Completist.yml`
**Schedule:** 1:00 AM PST (0 9 ** \* UTC)
**Capabilities:\*\*

- **Read:** Codebase for TRACKED_TASK, TRACKED_DEFECT, NotImplementedError, pass statements
- **Write:** Implementations for incomplete code
- **Constraint:** Creates PRs for review; does not merge directly.

### 5. Documentation Auditor (The Librarian)

**Role:** Documentation Maintainer
**Workflow:** `.github/workflows/Jules-Documentation-Auditor.yml`
**Schedule:** 1:30 AM PST (30 9 ** \* UTC)
**Capabilities:\*\*

- **Read:** Code and existing documentation
- **Write:** Updates to `docs/`, README files, docstrings
- **Mode:** "CodeWiki" - treats the codebase as a living encyclopedia.

### 6. Sentinel (The Guardian)

**Role:** Security Scanner
**Workflow:** `.github/workflows/Jules-Sentinel.yml`
**Schedule:** 2:30 AM PST (30 10 ** \* UTC)
**Capabilities:\*\*

- **Read:** Codebase for security vulnerabilities (OWASP Top 10)
- **Write:** Security fixes, dependency updates
- **Constraint:** Focuses on high-priority security issues only.

### 7. Auto-Refactor (The Architect)

**Role:** Code Improvement Specialist
**Workflow:** `.github/workflows/Jules-Auto-Refactor.yml`
**Schedule:** 3:00 AM PST (0 11 ** \* UTC)
**Capabilities:\*\*

- **Read:** Codebase for DRY violations, code smells
- **Write:** Refactoring improvements
- **Constraint:** One file per PR; preserves behavior.

### 8. Issue Resolver (The Fixer)

**Role:** GitHub Issue Worker
**Workflow:** `.github/workflows/Jules-Issue-Resolver.yml`
**Schedule:** 3:30 AM PST (30 11 ** \* UTC)
**Capabilities:\*\*

- **Read:** Open GitHub issues with appropriate labels
- **Write:** Code fixes, closes issues via PR
- **Constraint:** Only works on issues labeled for automation.

### 9. PR Compiler (The Consolidator)

**Role:** Pull Request Merger
**Workflow:** `.github/workflows/Jules-PR-Compiler.yml`
**Schedule:** 4:00 AM PST (0 12 ** \* UTC)
**Capabilities:\*\*

- **Read:** All open PRs from automation
- **Write:** Consolidated PRs combining multiple changes
- **Constraint:** Only merges non-conflicting automation PRs.

### 10. Auto-Rebase (The Diplomat)

**Role:** Merge Conflict Resolver
**Workflow:** `.github/workflows/Jules-Auto-Rebase.yml`
**Schedule:** 5:00 AM PST (0 13 ** \* UTC)
**Capabilities:\*\*

- **Read:** PR branches, main branch
- **Write:** Rebased branches, conflict resolutions
- **Constraint:** Labels PRs with "conflict" if manual intervention needed.

---

## 🛠️ GitHub CLI & Workflow Reference

Always use Github CLI for making pull requests.
Whenever you finish a task for the user, push it to remote.
NEVER try to use GitKraken or anything other than Github CLI for Pull request creation.
All pull requests should be verified to pass the ruff and mypy requirements in the ci / cd pipeline before they are created.

### For PR Creation

- Always check if PR already exists first using a single focused `gh pr list --state open` query for the current repository
- Use simple, concise titles and descriptions for initial creation
- Wrap GitHub CLI commands in powershell `-Command "..."`
- Use single quotes inside double quotes for string parameters

### For PR Management

- Use `gh pr view [number]` to get PR details and status
- Use `gh pr checks [number]` to see CI/CD status
- Use `gh run list --branch [branch-name]` to see workflow runs
- Check for failing checks and address them systematically

### For CI/CD Issue Resolution

- Identify failing checks using `gh pr checks`
- Examine workflow run logs using `gh run view [run-id]`
- Make fixes on the same branch and push to update the PR
- Verify fixes by checking updated CI status

### Command Templates for Future Use

```bash
# Create PR:
powershell -Command "gh pr create --title 'Your Title' --body 'Your description'"

# Check PR status:
powershell -Command "gh pr view [PR_NUMBER]"

# Check CI/CD status:
powershell -Command "gh pr checks [PR_NUMBER]"

# List recent runs:
powershell -Command "gh run list --branch [BRANCH_NAME] --limit 5"

# View specific run:
powershell -Command "gh run view [RUN_ID]"
```

---

## 🔍 Pre-Commit Quality Checks (MANDATORY)

### Before Creating ANY PR

**CRITICAL**: All code MUST pass linting checks locally before pushing. Failing to do so wastes CI resources and blocks PRs.

```bash
# Python files - run ALL of these before committing:
ruff check .                    # Linting errors
ruff check --fix .              # Auto-fix what can be fixed
ruff format .                   # Format code
ruff format .                   # Formatting
mypy .                          # Type checking (if configured)

# Verify no issues remain:
ruff check . && echo "✓ All checks passed"
```

### Common Python Linting Issues to Avoid

1. **Trailing whitespace on blank lines** (W293) - Use editor setting to strip trailing whitespace
2. **Unsorted imports** (I001) - Run `ruff check --fix` to auto-sort
3. **Line too long** (E501) - Break long lines, especially in data structures
4. **Missing type hints** - Add type annotations to function signatures

### Workflow/YAML Validation

Before modifying GitHub Actions workflows, validate syntax:

```bash
# Check YAML syntax (requires yq or python-yaml)
python -c "import yaml; yaml.safe_load(open('.github/workflows/your-workflow.yml'))"

# Or use actionlint if available
actionlint .github/workflows/
```

---

## ⚠️ Shell Scripting in Workflows (CRITICAL)

### Common Pitfalls to Avoid

1. **Unquoted variables with spaces**:

   ```bash
   # ❌ WRONG - breaks if TARGET contains spaces
   basename $TARGET

   # ✅ CORRECT - always quote variables
   basename "$TARGET"
   ```

2. **jq null coalescing operator**:

   ```bash
   # ❌ WRONG - // gets misinterpreted by shell
   jq 'first // "default"'

   # ✅ CORRECT - use if-then-else instead
   jq 'first | if . == null then "default" else . end'
   ```

3. **Heredocs in YAML**:

   ```yaml
   # ✅ CORRECT - use literal block scalar for multi-line
   run: |
     cat << 'EOF'
     Content here
     EOF
   ```

### Testing Workflow Changes

Before pushing workflow changes:

1. **Validate YAML syntax** locally
2. **Test shell commands** in isolation
3. **Check for unquoted variables** that might contain spaces
4. **Review jq expressions** for shell quoting issues

### Reference Documentation

See `Repository_Management/workflow-fixes/` for documented fixes and patterns to avoid.

---

### 🔄 Workflow & Automation Governance

Agents must refer to the [Workflow Tracking Document](docs/workflows/WORKFLOW_TRACKING.md) to understand available tools.
All workflows follow the Governing Workflow Guidance documented in the `Repository_Management` repository (see `docs/architecture/WORKFLOW_GOVERNANCE.md` in that repository).
The **GitHub Issue Tracker** is the primary authority for tasking and gap remediation. Check existing issues before starting work.

---

### 📂 Repository Decluttering & Organization

To maintain a clean repository root, all development-related documentation (summaries, plans, analysis reports, technical debt assessments, etc.) MUST be stored in the `docs/development/` directory.

- **DO NOT** create new `.md` files in the root unless they are critical project-wide files (e.g., README, AGENTS, CHANGELOG).
- Prefer creating issues for task tracking rather than temporary markdown files.

## Specification

This repository's specification is defined in `SPEC.md` at the repo root.
Read SPEC.md before making any changes. Update it when your changes
affect documented functionality, features, or architecture.

## ?? NETWORK & API HYGIENE (CRITICAL)

- **NO MASS POLLING**: Agents MUST NEVER use `gh pr list`, `gh issue list`, or arbitrary REST/GraphQL loops in a bulk manner to "scan" or "sweep" the repository fleet. Single, scoped repository lookups are allowed when needed (e.g., checking if a specific PR exists).
- **LOCAL FIRST**: You must rely on local .md files, previously generated issues.json artifacts, or user assistance to find task context.
- **NO PARALLELIZED GITHUB CLI**: Never write or execute Bash/PowerShell scripts that loop over multiple repositories to perform gh operations (e.g., automated PR merge scripts running in loops).
- **BATCHING**: If extracting remote information is absolutely necessary, use a single, focused, and batched query.
- **SILENT FAILURES**: If an API rate limit is hit, HALT NETWORK ACTIVITY IMMEDIATELY. Do not write retry-loops that further punish the API endpoint. Alert the user and pivot to local technical-debt resolution.
