# Vendored RM-CI-000 Timing Helpers

These files are copied byte-for-byte from Repository_Management commit
`820402ec16afac89e30f6674252ad9237352b8b4`; the dashboard imports only this
local copy and does not import a sibling checkout at runtime.

| File           | Git blob                                   |
| -------------- | ------------------------------------------ |
| `constants.py` | `8f0531cefea0a7dc5a111a58bb97a91ab40a67f1` |
| `metrics.py`   | `08c1dbfc8dce35caa4b4ec2e33ea3567491d4751` |
| `window.py`    | `1449a0d05e584d15eac4e88be1c020b25e9a05be` |

`job_metric_semantics.py` adapts malformed external timestamps to missing
measurements, while the vendored functions remain the source of timing math.

Tests pin SHA-256 fingerprints of these exact bytes. The local Ruff configuration
keeps upstream's 88-column formatting while retaining the dashboard lint rules.
