# Repository_Management companion (#2093)

Shipped in [Repository_Management#2284](https://github.com/D-sorganization/Repository_Management/pull/2284)
(merge `e524c89`). This directory is an offline mirror only.

1. `staff/mail_policy.yml` (copy from `staff-mail_policy.yml` here if diffing).
2. Validator alongside existing `staff/directives.yml` checks (same CI job
   that validates role YAML). Minimum rules:
   - `allowed_senders` is a list of mappings with `address` (email) and `enabled` (bool).
   - Optional comment keys are ignored.
   - Defaults when omitted: `require_dmarc_pass: true`, `intake_enabled: true`,
     `max_age_hours: 72`, `rate_limit_per_sender_per_hour: 12`, `max_message_bytes: 262144`.

Runner Dashboard reads this file from `STAFF_RM_ROOT/staff/mail_policy.yml` on
each RM sync (see `staff.rm_mail_policy`).
