Child issue for Epic #63. Implementing foundation for Identity and Authorization.

**Tasks:**
- Define `Principal` model and `config/principals.yml` schema + loader
- FastAPI `require_principal()` dependency and auth middleware
- GitHub OAuth login flow for human principals (callback, session cookie, CSRF-safe)
- Service-token minting, rotation, and revocation for bot principals
- Remove all hardcoded `"dashboard-user"` / `"dashboard-operator"` strings; fail closed when no principal is attached
- Frontend: "Acting as" indicator, login/logout, session expiry handling
