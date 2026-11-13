---
issue: 3296
summary: "Add persona role capsule store, <=8 KB cap, Barbara mirror, epoch+1 fallback ingest, and SENSITIVE metadata-only emit_shared projection (#3295 S5, #3296)."
---

Implements Slice S5 of #3295 (unblocking #3296 Bob port): adds `backend/staff/role_capsule.py` (`RoleCapsuleStore`, `emit_shared`, `barbara_mirror_relpath`, `CapsuleError`, `CapsuleStaleEpochError`) enforcing an 8 KB cap, secret/confidential redaction checks, monotonic epoch fencing on save and Grok fallback ingest (`epoch + 1`), and metadata-only `emit_shared()` projection for `SENSITIVE` roles.
