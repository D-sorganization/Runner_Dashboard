# Qualified deployment preflight — review before activation

Prepared 2026-09-30 for Runner_Dashboard issue #1834. No production service, root deployment authority, or GitHub environment was changed.

## Verified current state

- Main is protected. Windows validation PR #1833 merged b6cee8df; watchdog test PR #1837 merged f6dffd96.
- Live OGLaptop dashboard remains fadc115895a5a4d31deca41ecf86412065a8cb50, version4.10.0. JSON /livez and /readyz probes pass.
- GitHub environments inventory returned total_count0. The workflow-required oglaptop-production environment is absent.
- Root qualified deployment helper, its libraries and /usr/local/bin/cosign are absent.
- Draft release PR #1835 prepares4.10.1; no4.10.1 artifact published. Full Linux backend rerun passed 5,606 cases with zero failures/errors and 80 skips/expected failures; targeted sibling checks restored 14 additional skipped checks and passed (one pre-existing playbook fixture skip). Windows release-source validation and physical release acceptance remain outstanding.

## Concrete bootstrap inputs

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
- After approved publication, qualify exact signed/attested4.10.1 artifact and protected-main SHA, then dispatch existing qualified workflow in07:00–22:00 America/Los_Angeles window. Prove exact runner inventory, idle peers, unchanged scheduler capacity and rollback readiness according to runbook.
- Protected GitHub environment approval is the final production deployment boundary. No source-copy or direct service replacement.
