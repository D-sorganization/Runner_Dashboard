---
issue: 2902
type: feat
---

Add A3 hub local-ingress andon intake (`POST /api/v1/andon`) in `backend/andon/intake.py` with A2 fingerprint deduplication, bounded retry, and unit tests in `tests/test_andon_intake.py`.
