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
| `private_roles`     | list   | Private-role definitions (#3094, see below).       |

A fake example lives in `tests/fixtures/overlay/mock-overlay.json`; public CI
runs the suite against it alone.

## Private roles (#3094, private-roles slice 1)

Each entry in `private_roles` is an object:

| Key                    | Type   | Meaning                                              |
| ---------------------- | ------ | ----------------------------------------------------- |
| `role_id`              | string | Unique id; must match the requesting role exactly.    |
| `allowed_templates`    | list   | Template names this role may read. No bulk/listing.   |
| `providers`            | object | Host name to list of approved subscription CLI names. |
| `context_store_dir`    | string | Host-local directory of the encrypted context store. |
| `store_schema_version` | int    | Must equal the store's `.store-schema-version` file.  |

`backend/private_roles.py` is the single accessor
(`read_private_context`). It is called with the requesting role id, a target
template name, the host, and the provider CLI in use, and enforces, in
order: the role exists, the requester *is* that role (no panel, agent or
other role reads), the template is allowlisted, the host+provider pair is
approved, a host-local key is configured (`RD_PRIVATE_CONTEXT_KEY_PATH`), the
context store and its schema-version marker exist and match, and the
template file (Fernet-encrypted, `<template>.age`) decrypts.

Any failure returns a `PrivateContextUnavailable(role_id, reason)` value
instead of raising past the caller or returning context as if it loaded;
`reason` is one of a small fixed, non-sensitive vocabulary (for example
`role-not-found`, `not-owner`, `store-missing`, `version-skew`,
`decrypt-failed`). Callers must treat any such value as "private context
unavailable" and refuse context-dependent work, never as empty context.
Encrypted template fixtures and the host key are test-only (`tmp_path`);
nothing under `tests/fixtures/` holds a real store or key.

### `private_mode` (optional, issue #3250)

One additional, optional top-level key. Omitting it (every overlay before
#3250, and any overlay that doesn't want it) validates exactly as before.

| Key             | Type | Meaning                                                  |
| ---------------- | ---- | --------------------------------------------------------- |
| `enabled`        | bool | Private Mode on for this overlay.                          |
| `default_level`  | string | One of `PUBLIC`, `INTERNAL`, `SENSITIVE` (`backend/private_mode.py`). Unknown names fail closed. |

Absent, or `{"enabled": false, "default_level": "PUBLIC"}` — the same thing —
resolves to `private_mode.DISABLED`: classification stays `PUBLIC`
everywhere. See `docs/adr/0009-private-mode-classification-levels.md` and
`docs/architecture/private-mode-threat-model.md`.

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
