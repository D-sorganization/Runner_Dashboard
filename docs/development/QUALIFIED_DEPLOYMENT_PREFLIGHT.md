# Qualified deployment preflight — review before activation

Prepared 2026-09-30 for Runner_Dashboard issue #1834. No production service, root deployment authority, or GitHub environment was changed.

## Verified current state

- Security repair #1840 / PR #1841 merged33df0462 and integrated into this draft: exact Debian OpenSSL/PCRE2 security refresh and urllib3 2.8.0 targeted lock upgrade. Local real Docker/Trivy scan and71Linux hardening/HTTP tests pass;66release coherence/hardening tests pass after integration. Existing GitHub repair Docker job110220731442 remains queued; do not equate merge with scan completion. Latest release-head CI remains required.

- Main is protected. Windows validation PR #1833 merged b6cee8df; watchdog test PR #1837 merged f6dffd96.
- Live OGLaptop dashboard remains fadc115895a5a4d31deca41ecf86412065a8cb50, version4.10.0. JSON /livez and /readyz probes pass.
- GitHub environments inventory returned total_count0. The workflow-required oglaptop-production environment is absent.
- Root qualified deployment helper, its libraries and /usr/local/bin/cosign are absent.
- Issue #1838: direct existing WSL GitHub org API confirms OGLaptop registrations 7/8 absent; all eight local installations and registration files exist. Units 7/8 are inactive/dead (MainPID 0), with old registration IDs 223/224. Qualified deployment requires exact inventory 1–8, so this is another actual precondition blocker. No registration/credential/service changes attempted.
- Draft release PR #1835 prepares4.10.1; no4.10.1 artifact published. Full Linux backend rerun passed 5,606 cases with zero failures/errors and 80 skips/expected failures; targeted sibling checks restored 14 additional skipped checks and passed (one pre-existing playbook fixture skip). Full Windows release-source validation also passed 5,606 cases with zero failures/errors and 62 skips/expected failures. Docker CI and physical release acceptance remain outstanding.

## Concrete bootstrap inputs

### Python authority blocker — #1839

Ubuntu 26.04 supplies root-owned `/usr/bin/python3` 3.14.4; no root-owned Python 3.12 exists at the checked system paths. The qualified scheduler runtime requires exactly 3.12. User-managed Python 3.12.14 exists but is not root execution authority. No deployment was attempted.

Both Gemini advisory seats recommend a dedicated, verified root-owned Python 3.12 prefix and one closed selector shared by deployment and scheduler setup, with runtime preflight before filesystem/service mutation. This remains `judgement:design`; maintainer acceptance/relabel is required before implementation. Preserve distro Python and the backend 3.12 wheel ABI. Qualifying scheduler 3.14 alone would not resolve the separate backend installation boundary.

Prepared candidate (unprivileged cache only; not extracted, installed or executed):

- Official Astral release `20260929`, asset `cpython-3.12.14+20260929-x86_64-unknown-linux-gnu-install_only.tar.gz`.
- Local archive `/home/dieterolson/.cache/runner-dashboard-qualified-prep/cpython-3.12.14-20260929-linux-x86_64.tar.gz`.
- SHA-256 `06c90b93f419b63371c18f20fed0558a1a901f6518c3c24f755077e048447e7f`, matching the publisher asset digest and signed subject.
- GitHub attestation verification passed with repository, signer workflow `astral-sh/python-build-standalone/.github/workflows/release.yml` and source digest `4a7348fcaa53d686c894674a5071f52f8b54dac6` pinned. This signed workflow digest differs from the release tag target; do not conflate them. Verified SLSA provenance identifies release workflow run `36594441142`, attempt 1.
- Archive inspection passed: 4,534 members, 1,049 links; member/link destinations remain within the `python/` prefix, with no special devices. This is preparation evidence, not runtime qualification.
- Verification records: `python-3.12.14-provenance-pinned.json` and `.log` alongside the archive. A future approved operator install must reverify provenance/digest, use a root-owned fixed prefix with protected ancestors, and prove interpreter/venv/import/ownership contracts before enabling deployment authority.

Publisher sources: [release](https://github.com/astral-sh/python-build-standalone/releases/tag/20260929), [distribution layout](https://github.com/astral-sh/python-build-standalone/blob/main/docs/distributions.rst).

Clean regular Linux Git checkout: /home/dieterolson/.cache/runner-dashboard-qualified-prep/source-b6cee8df

Reviewed protected-main source commit: b6cee8df2d0c109ea4f0a4e5ed42aed319071d68

Bootstrap script SHA-256: 8a23a19d780fc671bb367f783559dbb9e37dd1e1c5873a86ed1c4ec1c1eecd78

Prepared Cosign binary: /home/dieterolson/.cache/runner-dashboard-qualified-prep/cosign-v3.0.6-linux-amd64

Cosign SHA-256: c956e5dfcac53d52bcf058360d579472f0c1d2d9b69f55209e256fe7783f4c74

Checksum independently obtained from pinned sigstore/cosign-installer action6f9f17788090df1f26f669e9d70d6ae9567deba6 action.yml; downloaded official v3.0.6 binary matches, version executes successfully. Source: https://raw.githubusercontent.com/sigstore/cosign-installer/6f9f17788090df1f26f669e9d70d6ae9567deba6/action.yml

Proposed root operation (not executed):

```bash
sudo bash /home/dieterolson/.cache/runner-dashboard-qualified-prep/source-b6cee8df/deploy/bootstrap-qualified-release-deploy.sh   --expected-commit b6cee8df2d0c109ea4f0a4e5ed42aed319071d68   --cosign-source /home/dieterolson/.cache/runner-dashboard-qualified-prep/cosign-v3.0.6-linux-amd64   --cosign-sha256 c956e5dfcac53d52bcf058360d579472f0c1d2d9b69f55209e256fe7783f4c74
```

Effects: install root-owned Cosign, qualified deployment helper/libraries/schedule, transaction directories and a narrow no-argument sudoers entry for dieterolson. The script does not restart services. Root authority activation requires operator review under docs/runbooks/qualified-release-deploy.md.

## Proposed protected environment (not created)

Name: oglaptop-production. Required reviewer: dieterolson (GitHub User198168927). Prevent self-review:true. Custom deployment branch policy:true, allow only main.

Environment payload:

```json
{
  "wait_timer": 0,
  "reviewers": [{"type": "User", "id": 198168927}],
  "prevent_self_review": true,
  "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}
}
```

Separate branch policy payload: {"name":"main","type":"branch"}. Verify server-returned reviewer/branch restrictions before any deployment workflow dispatch; never continue with an unprotected environment.

## Remaining release and deployment gates

- Full Linux suite, exact PR CI and coherent release metadata/schema.
- Issue #1805 explicitly requires physical-phone safe-area and screen-reader order before release, plus200% zoom and native keyboard open/close checks. Automated viewport clicks already passed; physical acceptance is pending.
- Owner acceptance/relabel for remaining Board design issues is separate; release notes do not claim them complete.
- Recover only missing standby registrations 7/8 under the existing recovery runbook, with owner approval, protected credential backups and rollback. Keep them offline and desired capacity four. Re-prove complete paginated inventory before qualification. Do not use broad setup or start dormant units.
- After approved publication, qualify exact signed/attested4.10.1 artifact and protected-main SHA, then dispatch existing qualified workflow in07:00–22:00 America/Los_Angeles window. Prove exact runner inventory, idle peers, unchanged scheduler capacity and rollback readiness according to runbook.
- Protected GitHub environment approval is the final production deployment boundary. No source-copy or direct service replacement.
