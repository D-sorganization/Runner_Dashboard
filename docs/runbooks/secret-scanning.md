# Secret scanning runbook

Tracking issue: #396.

This runbook covers the day-to-day operation of the secret-scanning gates
introduced for runner-dashboard:

- `gitleaks` and `detect-secrets` hooks in `.pre-commit-config.yaml`
- `gitleaks` job in `.github/workflows/ci-secrets.yml`
- `detect-secrets` baseline at `.secrets.baseline`
- `tests/test_no_secrets_in_repo.py` (fast in-tree grep)

## When the pre-commit hook fires

1. Read the finding line — gitleaks reports the file, line number and rule.
2. If the value is a **real** credential:
   - Do not commit. Rotate the credential immediately at the issuer
     (GitHub, AWS, OpenAI, Anthropic, Slack, etc.).
   - Replace it in the working tree with an environment-variable lookup or
     a placeholder (`os.environ["FOO"]`).
   - File an incident issue with the `security` label so the rotation is
     auditable.
3. If the value is a **placeholder** (test fixture, doc example):
   - Prefer renaming it so it no longer matches the pattern
     (e.g. `ghp_EXAMPLE_PLACEHOLDER`).
   - If the literal value is load-bearing, add it to the `[allowlist]`
     `regexes` table in `.gitleaks.toml` with a brief comment explaining why
     it is safe.
   - For detect-secrets, append `# pragma: allowlist secret` on the line.

## Refreshing the detect-secrets baseline

After auditing every entry, regenerate the baseline:

```bash
detect-secrets scan \
  --exclude-files '(package-lock\.json|uv\.lock|\.tsbuildinfo$|node_modules/|\.venv/|\.git/|\.secrets\.baseline)' \
  > .secrets.baseline

detect-secrets audit .secrets.baseline
```

`detect-secrets audit` walks every finding interactively so each one is
labelled `is_secret: true|false`. Commit the resulting baseline only after
all findings are marked `false` (placeholder) or rotated.

## Rotating the gitleaks pin

The pre-commit hook and the CI workflow both pin to a specific tagged SHA:

| Location                           | Field                | Update together |
| ---------------------------------- | -------------------- | --------------- |
| `.pre-commit-config.yaml`          | `rev:` for gitleaks  | yes             |
| `.github/workflows/ci-secrets.yml` | `GITLEAKS_VERSION`   | yes             |
| `.github/workflows/ci-secrets.yml` | `GITLEAKS_SHA256`    | yes             |

To bump:

```bash
git ls-remote --tags https://github.com/gitleaks/gitleaks.git vX.Y.Z
curl -fsSL "https://github.com/gitleaks/gitleaks/releases/download/vX.Y.Z/gitleaks_X.Y.Z_linux_x64.tar.gz" \
  | sha256sum
```

Apply the new SHA + tarball digest in the same PR; CI will fail closed if
they ever drift.

## Responding to a real leak

1. Rotate at the issuer first. Always.
2. Open a private incident issue (label `security`, `incident`).
3. If the secret reached `main`, request a Git history rewrite via the repo
   owner (`git filter-repo`) and force-push only after credentials are
   rotated and a new tag/release marker is cut.
4. Update `.gitleaks.toml` allowlist if and only if you intentionally want
   to suppress a future placeholder; never silence a real credential by
   adding it to the allowlist.
