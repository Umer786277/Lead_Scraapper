"""
Pipeline endpoints — trigger scrape + enrich, list runs.

The pipeline is CPU/IO-bound (Playwright). It runs in a thread-pool
executor so it doesn't block the async event loop.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import db
from api.deps import get_user_id
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


def _run_sync(searches, max_leads, headless, enrich_emails, user_id, run_id):
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
        )
        log.info(f"pipeline run_id={run_id} finished: {result}")
        # Tag all leads from this run with the user_id
        try:
            with db.get_conn() as c:
                c.execute(
                    "UPDATE leads SET user_id=%s WHERE run_id=%s AND user_id IS NULL",
                    (user_id, result.get("run_id", run_id)),
                )
        except Exception as e:
            log.warning(f"pipeline: could not tag leads with user_id: {e}")
    except Exception as e:
        log.error(f"pipeline run_id={run_id} FAILED: {e}", exc_info=True)
        try:
            db.finish_run(run_id, output_count=0, status="failed")
        except Exception:
            pass


@router.post("/run")
async def run_pipeline_endpoint(
    body: PipelineRunRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_user_id),
):
    searches = [s.model_dump() for s in body.searches]

    # Create the run record immediately so the frontend can show it
    run_id = db.start_run(
        "scrape_and_enrich",
        input_count=len(searches),
        metadata={
            "max_leads":     body.max_leads,
            "headless":      body.headless,
            "enrich_emails": body.enrich_emails,
            "searches":      searches,
            "user_id":       user_id,
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
        user_id,
        run_id,
    )

    return {"run_id": run_id, "status": "started"}


@router.get("/runs")
def list_runs(limit: int = 20, user_id: str = Depends(get_user_id)):
    return db.runs_summary(limit=limit)


# ── Recurring schedules (saturation-aware city rotation) ─────
class ScheduleCreate(BaseModel):
    niche: str
    country: str
    target_leads: int = 20


@router.post("/schedules")
def create_schedule(body: ScheduleCreate, user_id: str = Depends(get_user_id)):
    """Register a recurring scrape. Worker rotates through the country's
    cities, picking the most-deserving one each hour."""
    from scrape_planner import register_schedule
    try:
        return register_schedule(
            user_id=user_id,
            niche=body.niche,
            country=body.country,
            target_leads=body.target_leads,
        )
    except ValueError as e:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/schedules")
def list_schedules(user_id: str = Depends(get_user_id)):
    from scrape_planner import list_schedules as _list
    return _list(user_id)


@router.delete("/schedules/{schedule_id}")
def cancel_schedule(schedule_id: int, user_id: str = Depends(get_user_id)):
    from scrape_planner import cancel_schedule as _cancel
    ok = _cancel(user_id, schedule_id)
    return {"ok": ok}
