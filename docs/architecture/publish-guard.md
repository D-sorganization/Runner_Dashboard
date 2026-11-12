# Publish guard and fork-PR canary (issue #3080)

- `scripts/publish_guard.py`: every tracked file must match
  `config/publish/publish-allowlist.txt` and no path or line may match the
  shapes in `config/publish/forbidden-patterns.json` (no real values; private
  literals come from the overlay via `--extra-patterns`). `--self-test` is the
  canary. Output never echoes matched text. Single mode flag:
  `--mode report|enforce` / `RD_PUBLISH_GUARD_MODE`, default `report` until the
  #3082 backlog is zero, then `enforce` before cutover.
- `scripts/workflow_fork_canary.py` (enforcing): fails when a pull-request job
  can run fork code on a non-hosted runner unguarded, or a
  `pull_request_target` job checks out PR head code.
- Owner step: make `Publish Guard and Fork Canary` a required check.
