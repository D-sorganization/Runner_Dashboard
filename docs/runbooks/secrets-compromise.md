# Runbook: Secrets Compromise

A token, PAT, VAPID key, or other credential used by the dashboard or fleet
has been (or is suspected of being) leaked. Treat this as time-critical.

## Symptom

- A `GH_TOKEN`, `GITHUB_TOKEN`, registration token, VAPID private key, or
  similar credential appears in a public commit, log, screenshot, issue, or
  external paste.
- GitHub Secret Scanning fires on a push to any D-sorganization repo.
- Unexpected API activity is observed in `gh api /audit-log` or in
  `journalctl -u runner-dashboard` (e.g. requests from unfamiliar IPs).
- A teammate, contractor, or vendor with credential access has departed
  without a credential rotation having been performed.

## Severity

**P1** — every suspected leak is treated as a real leak. Rotate first,
investigate second.

## Detection

- `gh secret list` and `gh api /repos/D-sorganization/Runner_Dashboard/secret-scanning/alerts`
  for native secret scanning alerts.
- `mcp__github__run_secret_scanning` (or the same endpoint via gh) on the
  affected repo.
- Anomalies in the dashboard's recent action feed.
- Out-of-band reports from contributors.

## Diagnosis

```bash
# 1. Confirm the leak: check the commit, paste, or log directly.
gh api repos/D-sorganization/Runner_Dashboard/secret-scanning/alerts \
  --jq '.[] | {number,state,secret_type,created_at,html_url}'

# 2. Identify what the credential touches. For GH_TOKEN, check scopes:
gh auth status

# 3. Inspect recent dashboard env file metadata (do NOT print contents).
ls -la ~/.config/runner-dashboard/env
stat ~/.config/runner-dashboard/env

# 4. Look for anomalous use in dashboard logs (last 6 hours).
sudo journalctl -u runner-dashboard --since "6 hours ago" --no-pager \
  | grep -Ei 'denied|401|403|unauthor|invalid token'

# 5. Audit org actions for unexpected dispatches in the last 24h.
gh api -X GET 'orgs/D-sorganization/actions/runs' \
  --paginate --jq '.workflow_runs[] | select(.created_at > (now - 86400 | todate))
                  | {repo: .repository.full_name, name, actor: .actor.login, created_at}'
```

## Mitigation

Rotate immediately, then contain. Do all of the following, in order:

```bash
# A. Revoke the leaked credential at the source.
#    - For a GitHub PAT: revoke at https://github.com/settings/tokens
#    - For a fine-grained token: revoke under Settings → Developer settings.
#    - For an org-installation token: rotate the GitHub App's webhook secret.
#    - For VAPID keys: regenerate via deploy script and re-issue subscriptions.

# B. Replace the credential everywhere it is consumed.
#    Dashboard env file (chmod 600 always):
sudo install -m 600 -o "$USER" -g "$USER" /dev/null \
  ~/.config/runner-dashboard/env.new
# edit ~/.config/runner-dashboard/env.new with the new GH_TOKEN
mv ~/.config/runner-dashboard/env.new ~/.config/runner-dashboard/env
sudo systemctl restart runner-dashboard

# C. Refresh runner registration tokens if a registration token leaked.
gh api -X POST orgs/D-sorganization/actions/runners/registration-token

# D. Force-purge any in-memory caches that may hold the stale token.
sudo systemctl restart runner-dashboard
sudo systemctl restart runner-autoscaler

# E. If the leak was in a public commit, scrub history with care.
#    Prefer rotating the secret and leaving history intact unless the
#    secret cannot be revoked. Forced history rewrites are last-resort.
```

Notify the security owner via the operator channel; do not announce the
leaked value publicly.

## Resolution

- File a P1 issue (private repo if the leak is sensitive) capturing what
  leaked, where, when, the rotation evidence, and follow-up actions.
- Add a pre-commit hook locally and verify `gitleaks` / `bandit` would
  have caught the pattern; tighten if not.
- Confirm `bandit` rules and `pip-audit` step in `ci-standard.yml` did not
  miss anything obvious.
- Update `deploy/refresh-token.sh` if its rotation cadence is too long.
- Audit who has access to `~/.config/runner-dashboard/env` on every fleet
  host; revoke as needed.
- Schedule a follow-up rotation reminder (calendar event) within the
  credential's natural rotation window.
- File a postmortem and assign action items to a single owner with
  due dates.

## Postmortem Template

[`postmortem-template.md`](./postmortem-template.md)
