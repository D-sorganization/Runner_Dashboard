# #2897 publication record (for Barb / operator paste)

Repo artifact for the acceptance step that posts classifier output on **#2897**. The fleet App bot cannot comment on issues from the cloud agent host (403); Barb relays from this file.

**Script SHA:** record `git rev-parse HEAD` at merge time in the PR handoff.

## Top 3 causes (Oct 2025 backlog, replay + live snapshots)

| Rank | Cause | Evidence |
| --- | --- | --- |
| 1 | **`label_mismatch`** — jobs require labels no online runner carries | Snapshot 2 on #2897: 203 jobs, **100%** `label_mismatch`; dominant missing label **`d-sorg-fleet-public-heavy`** |
| 2 | **Missing public-heavy runner lane** | 0 online runners with `d-sorg-fleet-public-heavy` while standard fleet runners idle on other labels |
| 3 | **Integration visibility (403)** on org runner-group reads | Recorded as `api_findings`; does not prove absence of groups — policy/read scope follow-up |

## Per-class counts (snapshot 2, illustrative)

| Wait reason | Count | Share |
| --- | ---: | ---: |
| `label_mismatch` | 203 | 100% |
| Other classes | 0 | 0% |

## Suggested comment for #2897

```markdown
#2897 findings publication (read-only diagnostic)

- Classifier: `shared_scripts/queued_job_diagnostic.py` @ `<merge-sha>`
- Top causes: (1) label_mismatch / missing `d-sorg-fleet-public-heavy`, (2) idle standard fleet pool, (3) runner-group API 403 findings
- Full write-up: `docs/research/queued-job-backlog-2897-findings.md` + `docs/runbooks/queued-job-diagnostic.md`
- Reserved merge-group runner policy: **not indicated** for Oct 2025 data — stall was missing public-heavy capacity, not idle merge-group runners.
```
