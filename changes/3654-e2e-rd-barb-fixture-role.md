---
issue: 3654
summary: "Staff Console e2e: add rd-barb fixture role so the mobile chat-first home spec can send"
dl-state: in_review
next-step: "Merge; Frontend Tests on main should go green"
---

The mobile home composer targets PRIMARY_CHAT_ROLE rd-barb (#3493); the e2e fake roster lacked it, so Send stayed disabled.
