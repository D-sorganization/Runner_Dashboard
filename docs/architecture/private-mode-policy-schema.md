# Private Mode policy schema (open-source prep, OSS-1)

Issue #3273 (OSS-1), epic #3249. This is the open-source-prep slice for the
Private Mode epic: generic schema docs and a placeholder sample overlay, so
the public core repo stays reviewable without naming any real operator. It
never gates the epic's other slices.

## Where policy lives

Classification policy — which repos, paths or threads count as `INTERNAL` or
`SENSITIVE` (see the Level lattice added in PM-0, issue #3250) — is operator
data. It lives entirely under the private overlay's `policy_dir`
([core/overlay contract](overlay-contract.md), issue #3079), never in this
repo. The classification resolver landing in PM-1 (issue #3251) reads it
through the overlay accessor only (LoD); no other module opens policy files
directly.

## Generic schema (illustrative)

The shape below is validated and consumed by `backend/private_mode.py`
(`parse_classification_policy`, `load_classification_policy`,
`load_overlay_classification_policy`, and `resolve` — issue #3251, PM-1).
Every value here is a placeholder.

```json
{
  "default_level": "INTERNAL",
  "repos": [
    { "repo": "example-corp/widgets-public", "level": "PUBLIC" },
    { "repo": "example-corp/widgets-internal", "level": "INTERNAL" }
  ],
  "path_globs": [
    { "glob": "secrets/**", "level": "SENSITIVE" }
  ],
  "operator_marks": {
    "example-corp/widgets-internal#42": "SENSITIVE"
  }
}
```

- `default_level`: applied when nothing more specific matches; the overlay's
  own unknown-repo default is `INTERNAL` (fail toward privacy, never toward
  `PUBLIC`).
- `repos` / `path_globs`: most-specific match wins; an operator mark always
  overrides a path/repo rule (max-join per the Level lattice).
- Every identifier above (`example-corp/...`) is a placeholder; a real policy
  file with real repo or path names stays private, inside `policy_dir`.

## Sample overlay

[`tests/fixtures/overlay/sample-public-overlay.json`](../../tests/fixtures/overlay/sample-public-overlay.json)
is a complete, valid overlay built only from placeholder values (`example-*`
hosts, `.example.invalid` addresses) — safe to read in the public repo. It
validates through the same accessor as every other overlay
(`backend/overlay_contract.load_overlay`); see
`tests/test_sample_overlay.py`. It is separate from
`tests/fixtures/overlay/mock-overlay.json`, which CI uses to exercise the
contract itself.

## CI coverage

[`scripts/publish_guard.py`](../../scripts/publish_guard.py) (issue #3080)
runs on every pull request to `main`, including every Private Mode (PM) PR —
see [`.github/workflows/publish-guard.yml`](../../.github/workflows/publish-guard.yml).
It scans every tracked file, so the sample overlay above and any future
placeholder policy fixture are covered automatically; no separate PM-specific
CI step is needed.
