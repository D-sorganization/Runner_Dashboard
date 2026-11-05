# Unlabeled board issues — triage (2026-11-02)

Eleven open **unassigned, unlabeled** issues (excluding holds #2657/#2658/#2880 and blocked #3700/#3702). Specs live under `docs/agents/issue-specs/`. Apply taxonomy with `scripts/apply_rd_issue_labels.sh` on a bot host with issue write.

| Issue | Spec | Tractable | Open PR(s) |
| ----- | ---- | --------- | ---------- |
| #3818 | [3818-flow-metrics.md](issue-specs/3818-flow-metrics.md) | API + UI split | #3828, #3836 |
| #3819 | [3819-72h-open-pr-rule.md](issue-specs/3819-72h-open-pr-rule.md) | Claim gate + UI flag | #3837 |
| #3820 | [3820-credential-mirror-health.md](issue-specs/3820-credential-mirror-health.md) | Deploy audit + attention | #3829 |
| #3821 | [3821-scheduler-pause-ttl.md](issue-specs/3821-scheduler-pause-ttl.md) | Hub scheduler | #3830 |
| #3822 | [3822-missed-run-alert.md](issue-specs/3822-missed-run-alert.md) | Staff counters | #3832 |
| #3823 | [3823-agent-bus-reaper-readyz.md](issue-specs/3823-agent-bus-reaper-readyz.md) | Reaper health gate | #3833 |
| #3824 | [3824-antigravity-login-runbook.md](issue-specs/3824-antigravity-login-runbook.md) | Doc-only | #3829 |
| #3825 | [3825-barb-worker-vm-runbook.md](issue-specs/3825-barb-worker-vm-runbook.md) | Doc-only | #3829 |
| #3846 | [3846-redundant-pr-closer.md](issue-specs/3846-redundant-pr-closer.md) | CI script | #3847 |
| #3852 | [3852-desk-role-self-test.md](issue-specs/3852-desk-role-self-test.md) | Staff API | #3853 |

**Shepherd order:** #3847 → #3593 → #3835; board items above can merge in parallel when green.

**Winners (do not duplicate lanes):** #3792 → draft #3794; #3591/#3595 → #3593.
