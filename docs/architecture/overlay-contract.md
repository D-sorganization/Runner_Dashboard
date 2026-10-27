# Core/overlay contract

Issue #3079, epic #3077 (public core plus a private overlay).

The public core never names private hosts, people or paths. An operator
supplies them in a private overlay file kept outside the core repo (the
private `Runner_Dashboard-overlay` repo), and points `RD_OVERLAY_PATH` at it.
`backend/overlay_contract.py` is the only module that reads it; every other
module asks `get_overlay()` for a validated, immutable `Overlay`.

## Schema (version 1)

One JSON object. All keys are required and unknown keys are rejected.

| Key                 | Type   | Meaning                                            |
| ------------------- | ------ | -------------------------------------------------- |
| `schema_version`    | int    | Must equal `1`.                                    |
| `hosts`             | list   | Objects with exactly `name` and `address`.         |
| `agent_token_paths` | object | Agent name to token file **path** (never a token). |
| `roles_dir`         | string | Private role and policy files.                     |
| `policy_dir`        | string | Private policy files.                              |
| `private_docs_dir`  | string | Private docs.                                      |
| `private_mode`      | object | Optional (#3250). See below.                       |

### Optional `private_mode` (#3250)

When absent, Private Mode is **disabled** and classification defaults to
**PUBLIC**. Unknown keys under `private_mode` are rejected.

| Key                      | Type   | Default when omitted | Meaning                                      |
| ------------------------ | ------ | -------------------- | -------------------------------------------- |
| `enabled`                | bool   | `false`              | Master feature flag (off = no behaviour change). |
| `metric_route_templating`| object | —                    | Nested flag for metric route templating.       |
| `metric_route_templating.enabled` | bool | `false`     | Off by default; independent of master flag.    |

Policy (repo→level maps, provider allowlists) stays under `policy_dir`, not in
this block. See [ADR-0009](../adr/0009-private-mode-foundation.md).

A fake example lives in `tests/fixtures/overlay/mock-overlay.json`; public CI
runs the suite against it alone.

## Startup behaviour

| `RD_OVERLAY_PATH` | `RD_OVERLAY_REQUIRED` | Result                           |
| ----------------- | --------------------- | -------------------------------- |
| unset             | unset (default)       | No overlay; behaviour unchanged. |
| unset             | `1`                   | Refuse to start.                 |
| set, valid        | any                   | Overlay loaded and validated.    |
| set, invalid      | any                   | Refuse to start.                 |

Errors name keys and reasons only; they never echo overlay values or the
overlay path. `RD_OVERLAY_REQUIRED` stays off until public cutover. Existing
config reads move behind the accessor one category at a time in #3082.
