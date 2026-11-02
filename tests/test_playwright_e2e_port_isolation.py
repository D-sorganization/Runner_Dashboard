"""Issue #3738: the `Playwright E2E smoke tests` job fails on shared-network
self-hosted runners when a concurrent job (or a leftover server from a prior
job) already holds the hardcoded Vite dev-server port 5173:

    Error: http://localhost:5173 is already used, make sure that nothing is
    running on the port/url or set reuseExistingServer:true in
    config.webServer.

`reuseExistingServer` is deliberately wrong here (a job could then run
against another job's build), so each CI run must pick its own free port
for the Vite dev server instead of the historical hardcoded 5173.

Tracking: issue #3738.
"""

from __future__ import annotations

from pathlib import Path

_CONFIG = Path(__file__).parent.parent / "playwright.config.ts"


def _source() -> str:
    return _CONFIG.read_text(encoding="utf-8")


def test_ci_web_server_does_not_hardcode_port_5173() -> None:
    source = _source()
    assert "--port 5173" not in source, (
        "the Vite dev server must not pin a fixed port in CI: concurrent jobs "
        "on a shared-network self-hosted runner collide on it (issue #3738)"
    )
    assert "http://localhost:5173" not in source, (
        "the webServer readiness URL must not hardcode port 5173 either, or it "
        "stays coupled to the same collision as the command above"
    )


def test_ci_allocates_a_free_port_for_the_dev_server() -> None:
    source = _source()
    assert "createServer" in source and "net" in source, (
        "expected a free-port allocation helper built on node:net, so CI picks "
        "a genuinely unused port instead of guessing one"
    )
    assert "process.env.CI" in source, (
        "the dynamic port allocation should only kick in under CI; local dev "
        "keeps the stable default port for convenience"
    )


def test_web_server_command_and_url_share_the_same_dynamic_port() -> None:
    source = _source()
    assert "VITE_PORT" in source, "expected a single port variable reused by both the command and the url"
    assert "`npm run dev -- --port ${VITE_PORT}`" in source
    assert "`http://localhost:${VITE_PORT}`" in source


def test_ci_port_is_stable_across_repeated_module_evaluations() -> None:
    """Regression (follow-up to #3738, introduced by PR #3741): Playwright
    Test re-imports `playwright.config.ts` fresh in every worker process it
    spawns over the life of one job (new worker processes for new test
    files, retries, etc. — even with `workers: 1`). A bare `getFreePort()`
    call probes a brand-new ephemeral port on *every* import, so each
    worker's `BASE_URL` pointed at a different port than the one the single
    `webServer` process (resolved once, by the master process) actually
    bound to — `net::ERR_CONNECTION_REFUSED` on every navigation, across
    every open PR, regardless of what each PR's own diff touched.

    The resolved port must be memoized across process boundaries for the
    lifetime of a single CI job, and the cache key must be scoped to that
    job so concurrent jobs on a shared self-hosted runner don't collide on
    the same cached port (that would reopen #3738).
    """
    source = _source()
    assert "readFileSync" in source and "existsSync" in source, (
        "expected the CI port resolution to check for a previously cached "
        "port before probing a new one, so repeated module imports within "
        "the same CI job read back the same value instead of asking the OS "
        "for a fresh one every time"
    )
    assert "writeFileSync" in source, (
        "expected the first resolution in a CI job to persist its port so "
        "later worker-process imports can read it back"
    )
    assert "GITHUB_RUN_ID" in source and "GITHUB_JOB" in source, (
        "expected the cache key to incorporate CI-job-specific identifiers "
        "so concurrent jobs on a shared runner don't collide on the same "
        "cached port"
    )
