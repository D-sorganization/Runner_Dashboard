---
issue: 2419
summary: "Bind external browser OAuth authorization and code exchange to the configured callback origin."
---

## Changed

- External sign-in uses `DASHBOARD_BROWSER_PUBLIC_ORIGIN` for both GitHub authorization and code exchange, preserving the explicit OAuth origin allowlist and existing production readiness checks. Private sign-in retains its MagicDNS callback.
- OAuth state records its selected origin; origin/plane changes consume state without exchanging a code. External sign-in cannot use local fallback identity.
- Owner and bearer checks recognize the configured browser Host even when edge markers are absent.

## Deployment

- Before enabling public ingress, register the exact browser `/api/auth/callback` URL in the GitHub App and include that HTTPS origin in `DASHBOARD_OAUTH_ALLOWED_ORIGINS`. Existing private origin/callback settings stay separate. No tunnel or ingress flag is enabled by this change.

## Validation

- Actual authorization/callback regressions failed before implementation, including the public-host-only owner check.
