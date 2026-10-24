---
issue: 2593
summary: "Add local Whisper voice status and transcribe endpoints with tmpfs cleanup and bounds"
---
Add local Whisper `/api/v1/voice/status` and `/api/v1/voice/transcribe` endpoints with 0600 tmpfs cleanup, MIME/size/duration bounds, and silence hallucination suppression.
