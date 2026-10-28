# 0009. Private Mode: classification levels and the safe-disable contract

## Status

Accepted — PM-0 of epic #3249 (Board special session 2026-10-27, requested by
Dieter via Barb). Implemented in `backend/private_mode.py`, wired into the
`private_mode` overlay field in `backend/overlay_contract.py` (#3079). Later
slices (PM-1 onward) build on this without changing it.

## Context

Dieter asked for a Private Mode so the fleet can take on work for
repositories, runs and threads that carry sensitive enterprise-type
information, with the same RD quality gates, without ever putting that
content on a shared surface (hub logs, transcripts, panel threads, board
posts, metrics, GitHub logs or artifacts) and without silently stalling
blocked work.

The Board reviewed the epic and recommended three classification levels
rather than two, with the middle level ("INTERNAL") explicitly defined, so
that private-but-not-sensitive content (most of today's fleet-internal
chatter) has a home that is neither fully public nor held to SENSITIVE's
strict isolation. That recommendation is Dieter's open decision #2 in #3249;
this ADR implements the Board's recommendation as the shipped default,
reversible if Dieter rules otherwise.

PM-0's job is narrower than the rest of the epic: define the `Level` type and
the overlay schema field that carries it, and guarantee that turning the
feature on later never changes behaviour for anyone who leaves it off.

## Decision

1. **Three levels, a total order:** `Level.PUBLIC < Level.INTERNAL <
   Level.SENSITIVE` (`backend/private_mode.py`, an `IntEnum`). Joining two
   levels for the same content always keeps the more restrictive one — the
   property every later resolution slice (PM-1's `max_join`, in particular)
   depends on.
2. **Level semantics**, binding for every later slice:
   - `PUBLIC`: may appear on any surface, same as all content today.
   - `INTERNAL`: stays off public surfaces (public repo mirrors, public
     GitHub issues/PRs/comments, OSS artifacts) but may still reach the
     fleet's own private hub logs, transcripts and board. PM-1 migrates
     legacy (pre-flag) threads here, not to `PUBLIC`.
   - `SENSITIVE`: never reaches *any* shared surface — not hub logs, not
     transcripts, not panel threads, not board posts, not metrics, not
     GitHub logs or artifacts. Confined to private, ephemeral, RD-owned
     infrastructure built out in Wave B/C of the epic.
3. **Schema field:** `private_mode` is a new, optional, additive top-level
   key in the `private-overlay.v1` schema: `{"enabled": bool,
   "default_level": "PUBLIC" | "INTERNAL" | "SENSITIVE"}`. An overlay that
   omits the key, or no overlay at all, validates exactly as it did before
   this ADR.
4. **Safe-disable contract:** `private_mode.DISABLED` (`enabled=False,
   default_level=Level.PUBLIC`) is the value every flag-off path resolves
   to. No separate env var gates this — per the overlay contract, it is only
   readable through `overlay_contract.get_overlay()`, so there is exactly
   one way for any later module to ask "is Private Mode on" and no second
   implementation to drift from it (DRY).
5. **Fail closed, don't echo:** an unknown `default_level` name, or a
   malformed `private_mode` object, raises `OverlayError` naming the key and
   the reason only — never the rejected value — consistent with the rest of
   the overlay contract.

## Non-goals (this slice)

No vault, no audit store, no CI gate, no encryption, no admission policy, no
UI. Those are later slices (PM-1 through PM-21); PM-0 only ships the type and
the schema field they will all read.

## Consequences

**Easier:**

- Every later slice resolves classification through one typed, total-order
  enum instead of ad hoc strings, and reads it through the one overlay
  accessor instead of inventing a second config path.
- The flag-off path is a single named constant (`private_mode.DISABLED`),
  so "nothing changed when the flag is off" is one equality assertion in
  tests, not a sweep of every call site.

**Harder / cost:**

- Adding a fourth level later is a real, reversible-but-not-free change: it
  touches the total order every join/compare call site assumes, and needs
  its own ADR per the "Reversible" engineering principle.
- The three-levels-with-INTERNAL decision is still open per #3249's board
  review; if Dieter rules for two levels instead, this ADR is superseded and
  `Level.INTERNAL` and every call site that branches on it move with it.
