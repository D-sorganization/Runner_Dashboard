---
issue: 3000
summary: "Prevent personal Git credential helper fallback when fleet App minting fails"
---

Hermetic pytest defaults strip host ``HUB_FLEET_TOKEN``, hub URL, and loopback OAuth
flags so self-hosted runners do not force 401/503 on unrelated API tests; deploy and
staff unit tests assert personal ``ghp_`` tokens are never used when App mint fails.
