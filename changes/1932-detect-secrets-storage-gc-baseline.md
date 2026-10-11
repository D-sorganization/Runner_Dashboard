---
issue: 1932
summary: "CI detect-secrets baseline now covers the storage GC files added in #1928 (deploy/install-storage-gc.sh, tests/deploy/test_install_storage_gc.py); three Base64 High Entropy findings audited as systemd Environment= config text, not secrets (#1932)"
dl_state: "in_review"
next_step: "Merge the baseline PR; detect-secrets baseline diff job goes green on subsequent PRs."
title: "Baseline entries for storage GC files (#1928)"
owner: "claude"
branch: "chore/1932-detect-secrets-baseline"
paths: ".secrets.baseline"
---

Regenerated with detect-secrets 1.5.0 using the CI-printed command, then normalized (forward slashes, LF). Only results and generated_at changed: 3 new Base64 High Entropy entries, zero removed or changed. Committed plugins_used/filters_used kept verbatim (the regenerated filters dropped the is_baseline_file filter and .ci-ds-venv/ exclusion; the CI scan reads the committed config).
