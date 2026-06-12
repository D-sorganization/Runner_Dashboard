"""Autoscaler pool state and control routes (issue #755).

Exposes per-pool scaling state for the ControlTower NVMe and HDD pools.
The single read endpoint is intentionally simple and side-effect-free:
it assembles state from the already-evaluated autoscaler config constants
and the live systemd unit list, returning it in a structure the frontend
can render directly.

POST /api/autoscaler/pools/{pool}/config allows an operator to override
pool min/max counts at runtime (backed by env-var injection — the autoscaler
picks them up on the next reload).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from identity import Principal, require_scope
from pydantic import BaseModel, Field

log = logging.getLogger("dashboard.autoscaler_pools")
router = APIRouter(tags=["autoscaler"])

# ---------------------------------------------------------------------------
# Pydantic models (DbC — all POST routes require typed payloads per CLAUDE.md)
# ---------------------------------------------------------------------------

VALID_POOL_NAMES = frozenset({"nvme", "hdd", "default"})


class PoolScalingState(BaseModel):
    """Per-pool autoscaler state (read-only snapshot)."""

    pool: str
    min_online: int
    max_online: int
    default_online: int
    pattern: str
    labels: list[str]
    start_enabled: bool
    stop_enabled: bool
    pressure_metric: str  # human-readable name of the primary pressure signal
    cooldown_secs: int


class PoolsResponse(BaseModel):
    """Response envelope for GET /api/autoscaler/pools."""

    pools: list[PoolScalingState]
    cooldown_secs: int
    dry_run: bool


class PoolConfigPatch(BaseModel):
    """Runtime override for a pool's min/max online counts."""

    min_online: int = Field(..., ge=0, description="Minimum runners to keep online.")
    max_online: int = Field(..., ge=1, description="Maximum runners allowed online.")

    model_config = {"extra": "forbid"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_pool_state() -> list[PoolScalingState]:
    """Build the per-pool state list from autoscaler_config constants.

    Imported lazily so the router module is importable even if the autoscaler
    config modules haven't been resolved yet (e.g., during unit tests that stub
    the config at the module level).
    """
    # Late import to honour the LoD principle: this router does not reach
    # through the autoscaler internals at import time.
    import autoscaler_config as cfg  # noqa: PLC0415
    import runner_autoscaler as ra  # noqa: PLC0415

    states: list[PoolScalingState] = []

    for pool_name in ("nvme", "hdd", "default"):
        pool_cfg = ra._get_pool_config(pool_name)

        if pool_name == "nvme":
            pattern = cfg.NVME_PATTERN
            pressure_metric = "cache_pressure / nvme_disk_pct"
        elif pool_name == "hdd":
            pattern = cfg.HDD_PATTERN
            pressure_metric = "hdd_io_utilization / hdd_disk_pct"
        else:
            pattern = "actions.runner.*"
            pressure_metric = "cpu / mem / load / disk"

        states.append(
            PoolScalingState(
                pool=pool_name,
                min_online=pool_cfg["min_online"],
                max_online=pool_cfg["max_online"],
                default_online=pool_cfg["default_online"],
                pattern=pattern,
                labels=list(pool_cfg.get("labels", [])),
                start_enabled=bool(pool_cfg.get("start_enabled", True)),
                stop_enabled=bool(pool_cfg.get("stop_enabled", True)),
                pressure_metric=pressure_metric,
                cooldown_secs=cfg.COOLDOWN_SECS,
            )
        )

    return states


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/api/autoscaler/pools",
    response_model=PoolsResponse,
    summary="Get per-pool autoscaler scaling state",
    description=(
        "Returns the current autoscaler configuration and pressure rules for "
        "each pool (nvme, hdd, default). This is a read-only snapshot derived "
        "from environment variables evaluated at startup."
    ),
)
async def get_autoscaler_pools() -> PoolsResponse:
    """Return per-pool autoscaler state.

    Pre-condition: autoscaler_config is importable.
    Post-condition: response contains exactly three pools (nvme, hdd, default).
    """
    try:
        import autoscaler_config as cfg  # noqa: PLC0415

        pool_states = _load_pool_state()
        # DbC postcondition
        assert len(pool_states) == 3, "Expected exactly 3 pools in response"  # noqa: S101
        return PoolsResponse(
            pools=pool_states,
            cooldown_secs=cfg.COOLDOWN_SECS,
            dry_run=cfg.DRY_RUN,
        )
    except ImportError as exc:
        log.warning("autoscaler_config not available: %s", exc)
        raise HTTPException(status_code=503, detail="Autoscaler config unavailable") from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to build pool state: %s", exc)
        raise HTTPException(status_code=500, detail="Internal error assembling pool state") from exc


@router.post(
    "/api/autoscaler/pools/{pool}/config",
    summary="Override a pool's min/max online runner counts at runtime",
    description=(
        "Sets AUTOSCALER_{POOL}_MIN_ONLINE and AUTOSCALER_{POOL}_MAX_ONLINE "
        "environment variables in the current process. The autoscaler service "
        "reads these from its environment; a service reload is required to "
        "propagate changes to the running autoscaler loop. The dashboard "
        "reflects the new values immediately."
    ),
)
async def patch_pool_config(
    pool: str,
    body: PoolConfigPatch,
    _principal: Principal = Depends(require_scope("fleet.control")),  # noqa: B008 — issue #924
) -> dict:
    """Override pool scaling bounds at runtime.

    Pre-condition: pool must be one of the known pool names; min <= max.
    Post-condition: env vars updated; returned dict mirrors the applied values.
    """
    # DbC: pre-conditions
    if pool not in VALID_POOL_NAMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown pool {pool!r}. Valid pools: {sorted(VALID_POOL_NAMES)}",
        )
    if body.min_online > body.max_online:
        raise HTTPException(
            status_code=422,
            detail=f"min_online ({body.min_online}) must be <= max_online ({body.max_online})",
        )

    import os  # noqa: PLC0415

    prefix = pool.upper()
    os.environ[f"AUTOSCALER_{prefix}_MIN_ONLINE"] = str(body.min_online)
    os.environ[f"AUTOSCALER_{prefix}_MAX_ONLINE"] = str(body.max_online)

    log.info(
        "Pool config patched: pool=%s min_online=%d max_online=%d",
        pool,
        body.min_online,
        body.max_online,
    )

    # DbC postcondition: verify env vars were written
    assert os.environ.get(f"AUTOSCALER_{prefix}_MIN_ONLINE") == str(body.min_online)  # noqa: S101
    assert os.environ.get(f"AUTOSCALER_{prefix}_MAX_ONLINE") == str(body.max_online)  # noqa: S101

    return {
        "pool": pool,
        "min_online": body.min_online,
        "max_online": body.max_online,
        "status": "applied",
        "note": "Restart the autoscaler service for the new bounds to take effect in the scaling loop.",
    }
