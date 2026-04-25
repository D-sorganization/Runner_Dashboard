# Contributing to Runner Dashboard

Thank you for contributing to the D-sorganization Runner Dashboard. This guide
covers the development setup, agent coordination protocol, PR requirements, and
code style rules.

## Dev Setup

```bash
# Clone the repo
git clone git@github.com:D-sorganization/runner-dashboard.git
cd runner-dashboard

# Quick start — installs deps in a venv and starts the server on :8321
./start-dashboard.sh

# Stop
./stop-dashboard.sh

# Manual setup
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

Open http://localhost:8321 in your browser.

**Requirements:** Python 3.11+, a GitHub PAT with `repo` and `admin:org` scopes.

### Lint and format

```bash
ruff check backend/        # lint
ruff format backend/       # format
ruff check backend/ --fix  # auto-fix lint issues
```

### Type check

```bash
pip install mypy types-PyYAML types-requests
mypy backend/ --ignore-missing-imports
```

### Tests

```bash
pip install pytest pytest-cov
pytest tests/ -q --tb=short
```

### Pre-commit hooks

```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type pre-push
pre-commit run --all-files
```

## Agent Coordination Protocol

If you are an AI agent picking up an issue, you **must** acquire a coordination
lease before starting work to avoid redundant parallel effort.

**Lease protocol:**

1. Read `docs/agent-coordination-strategy.md` for full protocol details.
2. Post a lease comment on the issue before beginning work:
   ```
   lease: <agent-id> expires <ISO-8601 timestamp +2h>
   ```
3. Renew the lease every 2 hours if work is still ongoing.
4. Release the lease (or let it expire naturally) when the PR is opened or work
   is abandoned.

The Agent Lease Reaper workflow sweeps expired leases every 30 minutes.
`claim:<agent>` labels are automatically removed when leases expire without an
open PR.

**Agent priority order** (highest wins redundancy conflicts):
`user > maxwell-daemon > claude > codex > jules > local > gaai`

Before picking up an issue, confirm it is **pickable**: state is `open`, no
open PR references it, no active `claim:*` lease, and the issue's
`complexity:*` label is within your skill tier. Do not implement from issues
labelled `judgement:design` or `judgement:contested` — those require panel
review first.

## PR Requirements

1. **CI Standard must pass** (`ci-standard.yml`): ruff lint, ruff format, mypy
   type check, pip-audit, and the pytest suite all run on every PR.
2. **Update SPEC.md** if your PR adds or changes any documented behavior in the
   backend. Spec Check (`ci-spec-check.yml`) will fail if backend source files
   change without a corresponding SPEC.md update. Apply the `spec-exempt` label
   only if the change genuinely has no spec impact.
3. **Conventional Commits**: use `feat:`, `fix:`, `chore:`, `docs:` prefixes in
   commit messages.
4. **Branch naming**: use `feat/`, `fix/`, `chore/`, or `docs/` prefixes.

## Code Style

### Python (backend/)

- Python 3.11+ only. Use `match`/`case`, `X | Y` unions, `datetime.UTC`.
- All public functions must have full type annotations.
- Use the `logging` module via `log = logging.getLogger("dashboard")`. Never
  use `print()`.
- Prefer `pathlib.Path` over `os.path`.
- Keep functions focused; functions over ~50 lines are candidates for
  extraction.
- Imports: stdlib → third-party → local, sorted within groups (ruff enforces).
- No wildcard imports (`from module import *`).
- No debug statements (`breakpoint()`, `import pdb`).
- Constants in `UPPER_SNAKE_CASE` at module level.
- FastAPI route handlers should be `async` where I/O is involved.

### Frontend (frontend/)

- **No build step.** The frontend is a single `index.html` served directly.
- **No JSX.** Use `h()` (aliased from `React.createElement`) for all elements.
- **No npm / node_modules.** All dependencies are loaded from CDN via
  `<script>` tags.
- State managed with React hooks (`useState`, `useEffect`, `useCallback`).
- Tabs are top-level components defined in the single file.
- Styling via inline styles and a minimal CSS block in `<style>`.
- No TypeScript — plain JavaScript with JSDoc comments for complex types.
