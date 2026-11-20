---
issue: 4573
summary: "Review-arm passes head SHA and at-head PASS into shepherd base eligibility."
---

When merge-shepherd fails closed on missing head SHA, review_arm `decide()` must supply
`head_sha`, changed paths, and a fresh exact-head PASS once review-arm has confirmed
checks at head so base policy does not spuriously skip eligible candidates.
