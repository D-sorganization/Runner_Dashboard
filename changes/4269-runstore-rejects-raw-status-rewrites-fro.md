---
issue: 4269
summary: "RunStore rejects raw status rewrites from terminal rows (D-05 sticky terminals)"
---

PR #4481 remediation: keep the terminal-status check and write under one store
lock so a concurrent cancellation cannot be overwritten. A deterministic
unlock-interleaving regression test covers the race. Inline the status lookup to
preserve the existing 500-line module cap, and seed the lesson-provenance test's
failed row directly rather than rewriting a succeeded row.
