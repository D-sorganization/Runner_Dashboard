# Staff Console e2e fakes

Hermetic fixtures for the Staff Console end-to-end suite
(`tests/e2e/staff`, issue #1341). The backend under test is the real FastAPI
app. Only the provider CLIs, the roles and the identities are fakes, so no
test reaches a real provider, spends money or needs a login.

| Path                     | What it is                                                                                                          |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------- |
| `bin/claude`             | Fake `claude` CLI. It emits the real CLI's `stream-json` events (`system`, `assistant` per chunk, `result`).        |
| `roles/*.yml`            | Fixture roles: `barb` (front door), `e2e-analyst`, and `e2e-offline` (its only provider, codex, is not installed).  |
| `identity.json`          | Fixture principals `e2e-operator` (operator, `staff.approve`) and `e2e-viewer` (viewer) with fixture bearer tokens. |
| `start_staff_backend.py` | Seeds a throwaway state dir, points the backend at these fixtures with a `PATH` holding only `bin/`, then execs it. |

## Scenarios

A `[[e2e:<scenario>]]` directive anywhere in a message picks the fake's
behaviour; the last directive wins.

| Scenario        | Chat turn                                         | Work run                                                      |
| --------------- | ------------------------------------------------- | ------------------------------------------------------------- |
| _(none)_        | `Fake reply: <your message>`                      | `STAFF_RESULT: fake run finished`                             |
| `slow`          | Ten chunks, 0.4 s apart                           | —                                                             |
| `dispatch`      | Proposes `staff.dispatch` to `e2e-analyst`        | —                                                             |
| `dispatch-ask`  | As `dispatch`; the dispatched run asks a question | —                                                             |
| `dispatch-hang` | As `dispatch`; the dispatched run hangs           | —                                                             |
| `ask`           | —                                                 | Ends on a question until the prompt carries the user's answer |
| `hang`          | —                                                 | Sleeps a minute before finishing, so it can be cancelled      |
| `handoff`       | Hands the thread to `e2e-analyst`                 | —                                                             |
| `crash`         | Exit 1 with a traceback on stderr                 | —                                                             |
| `auth`          | Exit 1 with the CLI's expired-login message       | —                                                             |

## Running

```bash
npx playwright test -c tests/e2e/staff/playwright.config.ts
```

The fake is a shebang script, so the backend must run on Linux (CI or WSL).
On Windows, point Playwright at a WSL interpreter:

```bash
STAFF_E2E_PYTHON="wsl -e /path/to/venv/bin/python" npx playwright test -c tests/e2e/staff/playwright.config.ts
```

The fixture tokens and principals exist only in the throwaway state dir. They
are not credentials.
