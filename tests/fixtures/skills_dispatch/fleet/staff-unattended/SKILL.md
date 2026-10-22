---
name: fleet-staff-unattended
version: 1.0.0
description: Default skill for unattended staff dispatches in tests and hub nodes without RM seeds.
roles:
  - night-watch
  - barb
  - maintenance
  - ad-hoc
  - pr-remediator
  - issue-remediator
  - project-steward
  - r
  - fake
  - dummy
repos:
  - "*"
task_types:
  - staff-dispatch
---
Follow the injected fleet standards and role playbook for this unattended run.
