---
kind: fix
issue: 2322
---

Auto-refresh local Claude OAuth access tokens when a valid `refreshToken` is present on disk, include `jetski_state.pbtxt` in Antigravity refresh/liveness checks, suppress false-positive 48h `expiring` alerts when a refresh token is present, and run provider auth keepalive every 4 hours (#2322).
