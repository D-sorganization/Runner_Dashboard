"""Test execution and CI results routes."""

import logging

from fastapi import APIRouter

log = logging.getLogger("dashboard")
router = APIRouter()


@router.get("/api/heavy-tests/repos")
async def get_heavy_test_repos() -> dict:
    """List repos with heavy tests."""
    return {"repos": []}


@router.post("/api/heavy-tests/dispatch")
async def post_heavy_tests_dispatch(payload: dict) -> dict:
    """Dispatch heavy tests to local infrastructure."""
    return {"status": "queued"}


@router.post("/api/heavy-tests/docker")
async def post_heavy_tests_docker(payload: dict) -> dict:
    """Run heavy tests in Docker."""
    return {"status": "running"}


@router.get("/api/tests/ci-results")
async def get_ci_test_results() -> dict:
    """Get latest CI test results."""
    return {"results": []}


@router.post("/api/tests/rerun")
async def post_tests_rerun(payload: dict) -> dict:
    """Rerun failed tests."""
    return {"status": "queued"}
