---
issue: 2252
summary: "Reject bearer credentials at Cloudflare-marked or external-Origin ingress with a generic denial and audit log; preserve local bearer auth. No public tunnel is enabled."
branch: "codex/2252-ingress-token-guard"
paths: "backend/middleware.py,tests/test_middleware.py"
---
