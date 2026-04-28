# Linear Integration

This dashboard can read Linear workspaces and surface them in the Issues tab through the optional `/api/linear/*` API described in [`SPEC.md`](/C:/Users/diete/Repositories/_wt_runner_dashboard_issue241_20260428/SPEC.md).

## Prerequisites

- A Linear personal API key.
- A local dashboard checkout with write access to `config/linear.json` and `~/.config/runner-dashboard/env`.

## Configuration

1. Copy `config/linear.json.example` to `config/linear.json`.
2. Define one or more `workspaces` entries with:
   - `id`: local workspace identifier used by the dashboard.
   - `teams`: team keys to include, or `["*"]` for all teams.
   - `mapping`: mapping block name under `mappings`.
   - `default_repository`: fallback GitHub repo for downstream linkage.
   - `trigger_label`: GitHub label to use for dispatch handoff.
3. Define the mapping under `mappings` to translate Linear fields into derived GitHub-style labels.

Worked example:

```json
{
  "workspaces": [
    {
      "id": "personal",
      "teams": ["ENG"],
      "mapping": "default",
      "default_repository": "D-sorganization/runner-dashboard",
      "trigger_label": "dispatch"
    }
  ],
  "mappings": {
    "default": {
      "priority_labels": { "1": ["priority:p1"] },
      "estimate_labels": { "2": ["effort:m"] },
      "label_aliases": { "backend": ["backend"] }
    }
  }
}
```

## Setting The API Key

Use the Credentials tab and click `Set API key` on the Linear card, or write `LINEAR_API_KEY=...` into `~/.config/runner-dashboard/env`. Linear personal API keys begin with `lin_api_`.

## Using The Source Filter

When at least one configured workspace reports `auth_status=ok`, the Issues tab offers `GitHub`, `Linear`, and `Unified` sources. Unified view shows collapsed matches with source badges for both systems and a stats line showing GitHub count, Linear count, and collapsed pairs.

## Mapping Policy Customization

Add or edit `label_aliases` entries in `config/linear.json` to translate Linear labels into dashboard taxonomy. Restart the dashboard after changing mapping policy so fresh config is loaded.

## Troubleshooting

- `missing_env`: check `~/.config/runner-dashboard/env` and confirm `LINEAR_API_KEY` is present.
- Linear issues do not appear: confirm the configured `teams` filter and the selected source filter.
- Same issue shows twice: the Linear issue is not yet attached to its GitHub issue, so the unified collapse logic cannot pair them.
- `auth_failed`: the API key is invalid, revoked, or scoped to a different workspace.

## Webhook Setup

Webhook ingestion is intentionally out of scope for this phase. See issue `#242` for the follow-on webhook work.
