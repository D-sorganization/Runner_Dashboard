"""Shared assistant memory bank (append-only, cross-product context).

.. deprecated::
   The cross-agent Barb digest and ``GET /api/v1/memory/context-pack`` layer is retired for
   agent session startup (owner decision 2026-11-12). Prefer Barb continuity
   (``/api/v1/staff/barb/continuity``, turnover, leases, inventory), plus ``GET /api/priorities`` and
   ``GET /api/v1/staff/holds``. The HTTP API remains for legacy data and migration; do not add new
   integrations against context-pack or shared digests without an explicit owner decision.
"""
