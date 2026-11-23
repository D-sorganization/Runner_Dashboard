---
issue: 4630
summary: "Fail closed when lane-prompt checkout cannot honour requested role (no silent role substitution)."
---

Review 6016593353: reproduced an assignment role upsert during an active checkout.
Explicit-lane resumes now reuse the stored-receipt compatibility check and return
the structured role-unavailable conflict for a mismatched role. Regression coverage
includes idempotent and new-key resumes plus the HTTP 409 contract. Await
independent re-review; do not arm.
