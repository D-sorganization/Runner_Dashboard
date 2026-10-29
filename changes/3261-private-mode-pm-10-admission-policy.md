---
type: feat
---
Add Private Mode PM-10 admission policy controller (`backend/private_mode_admission.py`) with per-level capacity lanes (`PUBLIC`, `INTERNAL`, `SENSITIVE`), actual-destination allowlist enforcement, pre-spawn checks (exclusions, adapter bypass flags, keys, cloud approval, PM-9 audit), and explicit `READY / RUNNING / PARKED_POLICY / PARKED_CAPACITY / PARKED_AUDIT / FAILED / CANCELLED` states with sanitized reasons, owners, and deadlines (#3261).
