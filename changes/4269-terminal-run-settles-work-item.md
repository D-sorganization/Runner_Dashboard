---
issue: 4269
summary: "A terminal run never leaves its work item in_progress (D-08): a failed or blocked run sets the item to blocked with the run id and reason, a cancelled run returns it to open, and a retry run picks a blocked item up again. Only the item's latest run may move it."
paths: "backend/staff/run_link.py, backend/staff/work_items.py"
---
