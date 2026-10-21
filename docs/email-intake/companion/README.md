# Repository_Management companion (#2093)

This agent token cannot open PRs in `D-sorganization/Repository_Management`. Apply
the companion change manually:

1. Add `staff/mail_policy.yml` (copy from `staff-mail_policy.yml` in this directory).
2. Add a validator alongside existing `staff/directives.yml` checks (same CI job
   that validates role YAML). Minimum rules:
   - `allowed_senders` is a list of mappings with `address` (email) and `enabled` (bool).
   - Optional comment keys are ignored.
   - Defaults when omitted: `require_dmarc_pass: true`, `intake_enabled: true`,
     `max_age_hours: 72`, `rate_limit_per_sender_per_hour: 12`, `max_message_bytes: 262144`.

Runner Dashboard reads this file from `STAFF_RM_ROOT/staff/mail_policy.yml` on
each RM sync (see `staff.rm_mail_policy`).
