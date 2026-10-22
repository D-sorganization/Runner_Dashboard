---
issue: 2035
summary: "Opt-in WSL autoscaler installs fail closed unless a trusted installation receipt matches the expected local distro."
dl_state: "in_review"
next_step: "Owner installs and verifies the service inside the expected distro before enabling autoscaling; this change does not implement cross-distro control."
branch: "bot/issue-2035-runner-distro"
paths: "backend/autoscaler_config.py,backend/autoscaler_identity.py,backend/autoscaler_systemd.py,backend/runner_autoscaler.py,deploy/install-autoscaler.sh,deploy/runner-autoscaler.service,docs/runbooks/runner-offline.md,tests/test_autoscaler_config.py,tests/test_autoscaler_identity.py,tests/test_autoscaler_systemd.py,tests/test_runner_autoscaler.py"
---
