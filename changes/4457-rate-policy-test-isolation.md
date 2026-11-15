---
issue: 4457
summary: "Tests: reset the cached staff dispatch rate policy around each test, so a config-override test no longer changes the limit a later test in the same worker sees (main-red from #4460)"
paths: "tests/unit/test_staff_dispatch_rate_policy.py, tests/unit/test_staff_role_dispatch_rate_limit.py"
---
