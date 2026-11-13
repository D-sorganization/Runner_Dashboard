# Antigravity subscription route (#4239)

Strict panels (`require_verified_subscription`) pin each seat to its provider and
must not treat **model listing** (`agy models`) alone as proof of a **subscription-backed**
execution route, nor treat disk markers alone as sufficient when the live CLI probe fails.

## Supported local evidence (non-secret)

| Signal                                                                                                    | Meaning                                                        |
| --------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------- |
| `jetski_state.pbtxt` or non-expired `oauth_creds.json`                                                    | Subscription marker on disk                                    |
| OAuth session files present and not expired                                                               | Session may still run                                          |
| `agy models` exit 0 (`models_listing_ok`)                                                                 | CLI session live; required alongside disk subscription markers |
| `settings.json` / env (`ANTIGRAVITY_API_BASE`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `ANTIGRAVITY_API_KEY`) | Pay-per-use or ambiguous billing path                          |

## Verifier

`staff.antigravity_subscription_route` collects a bounded snapshot and returns a
fail-closed verdict requiring both disk subscription markers and a passing live
`agy models` probe (`models_listing_ok`). `panel_subscription_policy.check_provider_subscription`
uses it for `antigravity` only; other providers keep the OAuth probe path (#4223).

## Unsupported / unverified

- No dedicated `agy auth status` subcommand (#2209); we do not infer billing from model names.
- Custom API endpoints without fleet-side billing proof are rejected (`ambiguous_endpoint`).
- API-key routes are rejected (`api_key_route_not_subscription`).
- Positive disk markers with a failing `agy models` probe are rejected (`subscription_session_not_live`).
- This verifier does **not** configure credentials, change billing, or perform paid test calls.
