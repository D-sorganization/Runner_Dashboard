---
issue: 4084
summary: "Cap OGLaptop and DeskComputer runner-scheduler max_count at 6 (Dieter 2026-11-05)"
dl-state: in_review
next-step: "Merge; apply the same max_count:6 on live ~/.config/runner-dashboard/runner-schedule.json on OGLaptop and DeskComputer; confirm scheduler apply"
---

Dieter capped each host at six org runners. Seed `config/runner-schedule-oglaptop.json`
drops `max_count` from 8 to 6 (40GB resource_floor unchanged). Desk seed
`config/runner-schedule.json` raises the documented hard ceiling from 2 to 6 while
keeping interactive-safe window counts (1 daytime / 2 overnight). Live host JSON
still needs the same `max_count: 6` apply after merge.
