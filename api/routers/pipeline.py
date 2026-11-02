"""
Pipeline endpoints — trigger scrape + enrich, list runs.

The pipeline is CPU/IO-bound (Playwright). It runs in a thread-pool
executor so it doesn't block the async event loop.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import db
from api.deps import get_org_id, get_user_id, require_org_admin
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=2)


class SearchItem(BaseModel):
    niche: str
    city: str
    country: str


class PipelineRunRequest(BaseModel):
    searches: List[SearchItem]
    max_leads: int = 20
    headless: bool = True
    enrich_emails: bool = True


def _run_sync(searches, max_leads, headless, enrich_emails, org_id, run_id):
    """Executed in thread pool — calls the synchronous pipeline."""
    import logging
    log = logging.getLogger("pipeline")

    from pipeline import run_pipeline  # local import avoids Playwright at startup

    def on_event(kind, message, **extra):
        log.info(f"pipeline [{kind}] {message}")

    try:
        result = run_pipeline(
            searches=searches,
            max_leads=max_leads,
            headless=headless,
            enrich_emails=enrich_emails,
            on_event=on_event,
            org_id=org_id,
        )
        log.info(f"pipeline run_id={run_id} finished: {result}")
    except Exception as e:
        log.error(f"pipeline run_id={run_id} FAILED: {e}", exc_info=True)
        try:
            db.finish_run(run_id, output_count=0, status="failed", org_id=org_id)
        except Exception:
            pass


@router.post("/run")
async def run_pipeline_endpoint(
    body: PipelineRunRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_user_id),
    org_id: str = Depends(get_org_id),
):
    searches = [s.model_dump() for s in body.searches]

    # Create the run record immediately so the frontend can show it
    run_id = db.start_run(
        "scrape_and_enrich",
        input_count=len(searches),
        org_id=org_id,
        metadata={
            "max_leads":     body.max_leads,
            "headless":      body.headless,
            "enrich_emails": body.enrich_emails,
            "searches":      searches,
            "user_id":       user_id,
            "org_id":        org_id,
        },
    )

    loop = asyncio.get_event_loop()
    background_tasks.add_task(
        loop.run_in_executor,
        _executor,
        _run_sync,
        searches,
        body.max_leads,
        body.headless,
        body.enrich_emails,
        org_id,
        run_id,
    )

    return {"run_id": run_id, "status": "started"}


@router.get("/runs")
def list_runs(limit: int = 20, org_id: str = Depends(get_org_id)):
    return db.runs_summary(limit=limit, org_id=org_id)


# ── Recurring schedules (saturation-aware city rotation) ─────
class ScheduleCreate(BaseModel):
    niche: str
    country: str
    target_leads: int = 20


@router.post("/schedules")
def create_schedule(body: ScheduleCreate, user_id: str = Depends(get_user_id),
                    org_id: str = Depends(require_org_admin)):
    """Register a recurring scrape. Worker rotates through the country's
    cities, picking the most-deserving one each hour."""
    from scrape_planner import register_schedule
    try:
        result = register_schedule(
            user_id=user_id,
            org_id=org_id,
            niche=body.niche,
            country=body.country,
            target_leads=body.target_leads,
        )
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))

    # Don't make the first city wait for the next hourly tick (up to 59 min
    # of idle time) — kick the rotation job off now. It re-arms for its
    # normal 1h cadence after this run, same as any other tick.
    from api import worker_runner
    worker_runner.trigger_job("rotation")

    return result


@router.get("/schedules")
def list_schedules(org_id: str = Depends(get_org_id)):
    from scrape_planner import list_schedules as _list
    return _list(org_id=org_id)


@router.delete("/schedules/{schedule_id}")
def cancel_schedule(schedule_id: int, org_id: str = Depends(require_org_admin)):
    from scrape_planner import cancel_schedule as _cancel
    ok = _cancel(org_id, schedule_id)
    return {"ok": ok}
