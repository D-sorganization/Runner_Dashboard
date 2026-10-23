# Deferred External Validation

This folder holds Runner_Dashboard's deferred-validation catalog,
[`deferred-validation.json`](deferred-validation.json), under the fleet standard
[`docs/fleet-deferred-validation.md`](https://github.com/D-sorganization/Repository_Management/blob/main/docs/fleet-deferred-validation.md)
(Repository_Management#1687).

A record here is work whose remaining acceptance criteria need something no
coding agent can provide, such as owner-provisioned hardware. A record never
reports validation as done and never contains a measurement that was not taken.

## What is parked here

| Id       | Scope                                                    | Blocked on                |
| -------- | -------------------------------------------------------- | ------------------------- |
| DV-#2613 | Security scanner end-to-end acceptance on a real scan VM | Owner-provisioned scan VM |

## Who reviews it

The repository owner or the Board reviews each record and records
`reviewed_by` / `reviewed_on`. The original issue is closed as not planned only
after the record is merged, its `record_url` is verified on `main`, and that
review is recorded (publish, verify, then close).

## Validate

From a Repository_Management checkout:

```bash
python -m shared_scripts.deferred_validation --repo-root <path-to-Runner_Dashboard>
```
