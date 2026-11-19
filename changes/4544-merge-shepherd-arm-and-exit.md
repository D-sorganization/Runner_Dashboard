---
issue: 4544
summary: "Stop silent green on merge shepherd arm failure, add Tools to shepherd repos, drop line-cap gate, and accept exact-head verdicts."
---

Prevent silent-green on merge shepherd arm failure by returning non-zero when any arm fails, log per-PR arm outcomes, include Tools in SHEPHERD_REPOS, drop over_line_cap as a hard arming gate (advisory only), and accept verified exact-head review verdicts from comments, formal GitHub reviews, or staff store runs even when reviewDecision is not set.
