---
name: ask-barb
version: 1.0.0
description: Call the portable barb CLI to reach Barb from any agent app.
roles: ["*"]
repos: ["*"]
task_types: [ask-barb, staff-dispatch]
---
Use `python3 clients/fleet/barb.py ask "<question>"` for guidance, `barb project new` for intake, and `barb status` for fleet context.
