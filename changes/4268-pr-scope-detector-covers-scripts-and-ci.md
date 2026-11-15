---
issue: 4268
summary: "PR scope detector now runs the Python lane for PRs touching only scripts/, shared_scripts/, .github/, config/, fleet-hooks/, requirements.lock.txt or the Dockerfile (H-04). Source-only workflow change; it takes effect after merge."
paths: ".github/workflows/ci-standard.yml, tests/test_ci_config.py"
---
