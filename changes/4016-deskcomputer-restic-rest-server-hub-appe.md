---
issue: 4016
summary: "hub-a restic rest-server hub (append-only, Tailscale ACL grant for port 8000)"
dl_state: "in_review"
next_step: "Apply acl-fleet-backup.hujson in Tailscale admin, create htpasswd on hub-a, run install-rest-server.sh"
branch: "cursor/fleet-backup-rest-server-00d8"
---

CI: detect-secrets Secret Keyword false positive on `htpasswd_user` YAML keys; renamed to `rest_repo_user` (no baseline entry).
