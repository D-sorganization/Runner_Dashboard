# Private Mode — high-level threat model (PM-0, issue #3250, epic #3249)

This is the entry-level threat model for Private Mode. It names assets, trust
boundaries and abuse paths at the level PM-0 can address (classification
types and the safe-disable contract); later slices each own the control that
closes their row. See `docs/adr/0009-private-mode-classification-levels.md`
for the classification decision itself and `docs/architecture/overlay-contract.md`
for the overlay this epic composes with (#3079).

## Assets

- Content classified `SENSITIVE`: enterprise-type information in a private
  repository's threads, runs, and CI artifacts.
- Content classified `INTERNAL`: fleet-internal operational detail that is
  not meant for a public surface but is not itself sensitive.
- The classification decision itself (`Level`, the `private_mode` overlay
  field) — if an attacker can flip or hide it, every later control is moot.

## Trust boundaries

- **Public core vs. private overlay** (#3079): the public core repository and
  its CI must never see a classification level, host, or path that reveals
  what a private overlay protects.
- **Shared fleet surfaces vs. RD-owned private infrastructure**: hub logs,
  transcripts, panel threads, board posts, metrics, GitHub logs and
  artifacts are shared; `SENSITIVE` content must never cross into them. This
  is the boundary the epic's Goal statement names explicitly.
- **Flag off vs. flag on**: every node that has not opted into Private Mode
  must behave exactly as it does today (`PUBLIC` everywhere) regardless of
  what any other node's overlay carries — the safe-disable contract.

## Attacker capabilities considered at this slice

- A contributor or compromised dependency can read the public core repository
  and its CI logs (same threat `#3079`/`#3080` already assume).
  Mitigation at this slice: `private_mode` is additive and carries no private
  values itself (level names, not hosts or secrets); it ships flag-off, so
  there is nothing new to leak until a later slice turns it on.
- An operator or agent could misconfigure the overlay (wrong level name,
  malformed object). Mitigation: `private_mode.parse_private_mode` fails
  closed (`OverlayError`) on anything malformed, naming the key and reason
  only, never the rejected value — consistent with the rest of the overlay
  contract's no-echo rule.
- A future call site could compare levels inconsistently (treat `INTERNAL`
  as more restrictive than `SENSITIVE`, for example) and leak `SENSITIVE`
  content as if it were `INTERNAL`. Mitigation: `Level` is a single
  `IntEnum` total order; there is exactly one ordering, and `parse_private_mode`
  is the only constructor, so there is no second enum or string literal set
  to drift out of sync (DRY).

## Explicitly out of scope at this slice

- Everything that actually keeps `SENSITIVE` content off a shared surface at
  runtime (sink adapters, encrypted transcripts, the private CI controller,
  zero-trust node identity) — those are PM-2 through PM-18, not PM-0.
- Key management, admission policy, audit, and separation of duties — PM-4
  through PM-13.
- Certification claims: the controls land in this epic are *SOC 2-style*,
  never evidence of an actual audit (epic non-goal, repeated here so no
  later slice's docs imply otherwise).

## Residual risk accepted for now

Until Wave B lands, an overlay can declare `private_mode.enabled = true`
with no slice yet honoring it — turning the flag on today does nothing
protective. This is intentional (Wave A ships the type before the controls)
but worth flagging: do not advertise Private Mode as available to an
operator before PM-21's supervised dry run and Dieter's sign-off.
