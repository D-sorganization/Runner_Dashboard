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

- **Vite + React + TypeScript.** Source lives in `frontend/src/`; the
  production bundle is produced by `vite build` and consumed by the
  FastAPI backend as static assets. `frontend/index.html` is the Vite
  entry HTML and mounts `/src/main.tsx`.
- **TypeScript first.** New components are `.tsx`; shared logic and
  hooks are `.ts`. The legacy single-file `App.tsx` survives at
  `frontend/src/legacy/App.tsx` during migration but is not where new
  features land.
- **Use JSX/TSX.** Write components as JSX; do not hand-roll
  `React.createElement` (`h()`) calls outside of the legacy folder.
- **npm dependencies.** Add packages via `package.json` and the
  committed lockfile; do not introduce ad-hoc CDN `<script>` tags.
- State managed with React hooks (`useState`, `useEffect`,
  `useCallback`) and typed contexts.
- Tabs live as standalone components under `frontend/src/pages/`.
- Styling via the design tokens in `frontend/src/design/`, primitives in
  `frontend/src/primitives/`, and `frontend/src/index.css`.
- Honor the performance budget in `frontend/perf-budget.json`; it is
  enforced by `tests/test_frontend_perf_budget.py`.
