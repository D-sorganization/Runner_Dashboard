---
issue: 4622
summary: "Bound job-board PR lookup and fail closed on incomplete, malformed or unexamined search evidence; search titles and bodies."
---

Review 6016592908: reproduced unsafe listing and title-only lookup failures before
the fix. Search still uses one bounded request; uncertain evidence excludes the
issue and sets completeness false. Independent re-review is required; do not arm.
