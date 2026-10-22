---
name: fleet-meaningful-tests
version: 1.0.0
description: Write meaningful tests with real failure data (test-hygiene).
roles: [issue-remediator, pr-remediator, test-hygiene]
repos: ["*"]
task_types: [write-tests, staff-dispatch]
---
TDD first; no vacuous assertions; cover the behavior you changed.
