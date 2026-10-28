# Activity ingestion v1

`POST /api/v1/activity/events` requires the authenticated `activity.write`
scope and accepts a JSON array of canonical `ActivityEnvelope` v1 records.
It returns the durable redacted records only after the whole batch commits.
The wire body is limited to 256 KiB and 100 events; an empty or malformed
batch returns 422, while oversized bodies or batches return 413.

Every event's `principal` must equal the authenticated caller's ID. The first
committed event binds its source (source type plus opaque node ID) and session
ID to that principal. Subsequent writes cannot take another principal's source
or session (403), including sessions created through the existing local store
API. Producers must use opaque identifiers without secrets or network addresses.

The hub applies the shared staff redactor recursively to all string metadata,
payload keys and payload values, and stamps `redaction_version: 1`; producer
version claims are ignored. Redaction collisions or invalid resulting envelopes
return 422. Validation errors never reflect the raw body.

New events consume a per-principal rolling 60-second quota of 1,000 events and
1 MiB of serialized redacted metadata. A quota failure returns 429 with
`Retry-After: 60`. Quotas persist across restarts. Identical retries of
`(source, session_id, seq)` return the stored record without consuming quota;
conflicting content or reused event IDs return 409.

Binding checks, deduplication, quota accounting and insertion share one SQLite
immediate transaction. A conflict, authorization failure or quota failure rolls
back every new event and quota charge in that batch. Concurrent instances use
the same database transaction boundary. This endpoint neither dispatches work
nor changes deployment or release state.
