# Public repository secret scanning

Issue #3081, epic #3077 (slice 3). The public core uses the shared pattern list
in `.gitleaks.toml`; the same file is used by the staged pre-commit hook and
the full-history `CI Secrets` workflow. Keep those paths in sync when changing
the scanner configuration.

## One-time inventory

Create a private directory outside the checkout, then run the inventory from
the repository root:

```bash
install -d -m 700 "$HOME/private/rd-secret-inventory"
python3 scripts/scan_history.py --dry-run
python3 scripts/scan_history.py \
  --report "$HOME/private/rd-secret-inventory/initial.md"
```

The scan uses the complete local Git history. `gh` must be signed in with
read access to Actions, repository forks, and issue/PR discussions. The script
counts Actions artifacts and workflow runs, forks, issue/PR comments, and
review comments; it does not download artifacts, logs, or comment bodies. It
writes only sanitized rule/file/commit/line references and counts. The report
path must be outside the repository and its directory must be private. Keep
the report private; never copy scanner values into issues, pull requests, or
logs.

Review **Settings → Actions → General → Artifact and log retention** and add
the configured retention period in days to the private report. GitHub exposes
artifact expiration metadata, but the repository's configured log-retention
setting must be confirmed by an owner. Add only confirmed credential
categories and rotation status to the rotation list, never credential values.

## Owner setup

For the published repository, an owner should open **Settings → Code security
and analysis** and enable **Secret scanning** and **Push protection**. Confirm
the settings are active after publishing. Do not enable paid scanning
services as part of this work.
