---
type: feat
---
Add Private Mode PM-15 shared ephemeral sandbox executor (`backend/ci/private_sandbox_executor.py`) extracted from `vuln_scan` hardening patterns, enforcing no host mounts or reusable credentials, documented phase-A egress allowlist with phase-B `--network=none` zero-egress, read-only toolchain layer, and unconditional teardown (#3266).
