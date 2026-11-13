---
type: feat
---
Complete Activity R1 (`#2874`) by provisioning `agent-cursor` as a `checkin-only` (`activity.checkin`) repo-managed principal without read/chat scopes, bounding `POST /api/v1/activity/checkins` wire bodies to `_CHECKIN_MAX_BYTES`, and covering strict kind/scope/wire-cap tests.

