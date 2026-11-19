---
issue: 4270
summary: "Job-board fences and lease TTL rows are stored in the claim-state database, so a restart no longer strands holders or hides expired leases from the sweeper (B-09)"
paths: "backend/coordination/job_board_rows.py, backend/coordination/claim_state.py, backend/coordination/jobs.py, backend/coordination/job_board_lease.py, tests/unit/test_job_board_durable_state_4270.py"
---
