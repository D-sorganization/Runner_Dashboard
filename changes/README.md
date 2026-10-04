# Change fragments

Every pull request adds **one** file, `changes/<issue>-<slug>.md`, instead of
editing the `SPEC.md` change log, `docs/development/DEVELOPMENT_LOG.md` or
`docs/development/HANDOFF.md`. Those three files are shared by every open PR, so
each merge to `main` used to make every other PR conflict
(`MERGE_CONFLICT` on the change-log row). A fragment is a new file per PR, so it
cannot conflict.

Origin: [Repository_Management#1894](https://github.com/D-sorganization/Repository_Management/issues/1894)
(RM-5), part of [Repository_Management#1889](https://github.com/D-sorganization/Repository_Management/issues/1889).

## Write one

```bash
python scripts/changes_fragment.py new --issue 1894 \
  --summary "One line for the SPEC.md change-log row"
# live work also names its development-log state:
python scripts/changes_fragment.py new --issue 1894 --summary "..." \
  --dl-state in_review --next-step "Merge the PR." --branch feat/1894-x
python scripts/changes_fragment.py validate   # check every fragment
```

A fragment is front matter plus an optional markdown body (the handoff notes):

```markdown
---
issue: 1894
summary: "One line for the SPEC.md change-log row"
dl_state: "in_review"
next_step: "Merge the PR."
branch: "feat/1894-x"
---

Optional handoff notes.
```

Rules enforced by `validate` (and the `changes-fragment` pre-commit hook):

- The file name is `<issue>.md` or `<issue>-<slug>.md` and starts with the
  `issue:` number; the slug is lowercase.
- `summary` is one non-empty line with no `|` (it becomes a table cell).
- `dl_state` is optional; a live state needs `next_step`, and `in_progress` /
  `in_review` need `branch`. `parked` is edited into the log directly.
- No secrets, no unedited placeholders.

## After merge

`collate-changes.yml` (shipped separately, Repository_Management#1894) folds each
fragment into the shared files, keyed by the PR number that added it: one
`| date | #<PR> | summary |` row in the `SPEC.md` change log and the
`DL-#<issue>` development-log entry updated in place. It then deletes the
fragment. Collation is idempotent. Until that workflow is merged, fragments
accumulate here and can be collated by hand:
`python scripts/changes_fragment.py collate --pr <N> changes/<file>.md`.

## Transition

Until Spec Check accepts a fragment in place of `SPEC.md` (workflow PR for
Repository_Management#1894), a PR that changes source still adds its one
`SPEC.md` row. Collation matches rows by PR number and never duplicates one.

## Scope

The modules (`scripts/changes_fragment*.py`, `development_log.py`,
`handoff_validator.py`, `spec_changelog.py`) are vendored from Repository_Management
`Project_Template/shared_scripts/`; re-sync them, do not fork. Runner Dashboard
never imports them from the sibling repo at runtime.
