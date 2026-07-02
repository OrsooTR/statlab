"""System endpoints: version info and in-app updates."""
from __future__ import annotations

from fastapi import APIRouter

from . import jobs, updater
from .config import APP_NAME, APP_VERSION

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/info")
def info() -> dict:
    return {"app": APP_NAME, "version": APP_VERSION,
            "frozen": updater.is_frozen(), "repo": updater.REPO}


@router.get("/check-update")
def check_update() -> dict:
    return updater.check_update()


@router.post("/apply-update")
def apply_update() -> dict:
    job = jobs.manager.submit_thread(
        "system-update", lambda progress: updater.apply_update(progress))
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.manager.get(job_id)
    if job is None:
        return {"status": "error", "error": "job not found"}
    return job.snapshot()
