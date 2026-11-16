---
issue: 4268
summary: "The CommonJS helpers under .github/scripts are named .cjs so they load under the root ES-module package (labels-sync.yml failed on main with 'require is not defined'), and their two node:test files now run in the Python suite (H-20)."
paths: ".github/scripts/labels-sync.cjs, .github/scripts/issue-backfill.cjs, .github/scripts/labels-sync.test.cjs, .github/scripts/panel-review.test.cjs, .github/workflows/labels-sync.yml, .github/workflows/taxonomy-rollout.yml, .github/workflows/issue-taxonomy-backfill.yml, tests/test_github_scripts_gate_4268.py"
---
