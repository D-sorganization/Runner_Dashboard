# CI Job Timing Metrics

The CI metrics endpoints expose a bounded view of persisted GitHub Actions job
timings. `GET /api/ci/metrics` supports repository, event, runner-label, or
inferred-host grouping over a 1–90 day UTC window. `POST /api/ci/metrics/collect`
manually advances the collector; collection is not started automatically.

Queue duration follows the shared RM-CI-000 contract: it is the elapsed time
from job creation to runner start. It can include workflow `needs:` dependency
wait as well as time waiting for a runner, so it is not a pure runner-queue
measurement. Execution duration is runner start to job completion. Only rows
with a usable job `created_at` can enter the rolling window; rows without it
remain stored but are excluded from that window. Within the window, a missing
or invalid start/completion timestamp is excluded from that metric's percentile
sample. The separate sample counts show how many values are available, while
coverage reports collection completeness rather than timestamp quality.

The collector requests all job attempts for each workflow run and pages through
repositories, runs, and jobs under per-invocation limits. It stores each page
idempotently and resumes an unfinished jobs list from its saved page, bound to
the run ID, attempt, and update timestamp. Coverage reports collection caps,
partial reasons, and runs that remain incomplete. A later successful page does
not erase a data-quality reason recorded on an earlier page.
The collection response distinguishes a completed traversal from complete
coverage: `traversal_complete` can be true while `complete` remains false because
`incomplete_runs` is nonzero.

Label groups are non-additive: a job carrying multiple labels appears in each
corresponding label group. Host groups use the existing runner-name inference;
recognized runner pools are canonicalized, while an unrecognized non-empty
runner name is retained as supplied and a missing name is shown as `Unknown`.
Percentiles use the shared inclusive linear interpolation definition. The
endpoint returns separate job, queue sample, and execution sample counts so
missing timing values are visible.
