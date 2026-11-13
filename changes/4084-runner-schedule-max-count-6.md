---
issue: 4084
summary: "Cap per-node runner-scheduler max_count at 6 (Dieter 2026-11-05)"
---

Dieter capped each node at six org runners. The node-specific seed drops
`max_count` from 8 to 6 (40GB resource_floor unchanged). The hub seed
`config/runner-schedule.json` raises the documented hard ceiling from 2 to 6 while
keeping interactive-safe window counts (1 daytime / 2 overnight). Live host JSON
still needs the same `max_count: 6` apply after merge.
