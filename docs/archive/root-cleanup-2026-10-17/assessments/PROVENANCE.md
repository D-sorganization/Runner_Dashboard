# Provenance — `assessments/` root-cleanup archive (2026-10-17)

Archived from the repository root (`assessments/`) by the Sanitation Engineer
pass on 2026-10-17.

## Go-public removal (2026-10-28, issue #3305)

The per-repo assessment JSON, comprehensive fleet bundle, generator scripts, and
open-issues scratch dumps that described **private sibling repositories** were
removed forward-only so they would not ship when this repository goes public.
Runner_Dashboard's canonical assessment copy remains under `docs/assessments/`.

## Historical note

The removed tree was a one-off staging area for a fleet-wide 16-criterion
assessment run orchestrated from this repo in April 2026. Eleven of the twelve
repos in that run were private siblings; only Runner_Dashboard belongs here.
