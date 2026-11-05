"""
Pipeline endpoints — trigger scrape + enrich, list runs.

The pipeline is CPU/IO-bound (Playwright). It runs in a thread-pool
executor so it doesn't block the async event loop.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List

import db
import requests
from api.deps import get_org_id, get_user_id, require_org_admin
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()
_executor = ThreadPoolExecutor(max_workers=2)
COUNTRIESNOW_API = "https://countriesnow.space/api/v0.1"
_RUN_LOG_MAX_LINES = 500


def _event_line(event) -> str:
    created = event.get("created_at")
    if isinstance(created, datetime):
        stamp = created.astimezone().strftime("%H:%M:%S")
    else:
        stamp = str(created or "")[11:19]
    return f"{stamp} [{event.get('kind')}] {event.get('message')}"


class SearchItem(BaseModel):
    niche: str
    city: str
    country: str


class PipelineRunRequest(BaseModel):
    searches: List[SearchItem]
    max_leads: int = 20
    headless: bool = True
    enrich_emails: bool = True


class CountryCitiesRequest(BaseModel):
    country: str


@router.get("/locations/countries")
def list_location_countries(org_id: str = Depends(get_org_id)):
    try:
        response = requests.get(f"{COUNTRIESNOW_API}/countries/iso", timeout=15)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(
            status_code=502, detail="Unable to load countries from CountriesNow."
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise HTTPException(
            status_code=502, detail="CountriesNow returned an invalid country list."
        )
    countries = sorted({
        item["name"].strip()
        for item in data
        if isinstance(item, dict) and isinstance(item.get("name"), str)
        and item["name"].strip()
    })
    if not countries:
        raise HTTPException(
            status_code=502, detail="CountriesNow returned an empty country list."
        )
    return {"countries": countries}


@router.post("/locations/cities")
def list_location_cities(
    body: CountryCitiesRequest,
    org_id: str = Depends(get_org_id),
):
    country = body.country.strip()
    if not country:
        raise HTTPException(status_code=422, detail="Select a country.")
    try:
        response = requests.post(
            f"{COUNTRIESNOW_API}/countries/cities",
            json={"country": country},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Unable to load cities for {country} from CountriesNow.",
        ) from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise HTTPException(
            status_code=502, detail="CountriesNow returned an invalid city list."
        )
    cities = sorted({city.strip() for city in data if isinstance(city, str) and city.strip()})
    if not cities:
        raise HTTPException(
            status_code=404, detail=f"CountriesNow returned no cities for {country}."
        )
    return {"cities": cities}


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
            run_id=run_id,
        )
        log.info(f"pipeline run_id={run_id} finished: {result}")
    except Exception as e:
        log.error(f"pipeline run_id={run_id} FAILED: {e}", exc_info=True)
        try:
            import run_events
            run_events.record(run_id, org_id, "error", f"Pipeline failed: {e}")
            run_events.flush()
        except Exception:
            pass
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


@router.get("/runs/{run_id}/events")
def run_events(run_id: int, org_id: str = Depends(get_org_id)):
    """Live log lines for a scrape run started by this API process."""
    run = db.get_run(run_id, org_id=org_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found.")
    events = db.list_run_events(run_id, org_id=org_id, limit=_RUN_LOG_MAX_LINES)
    return {
        "run_id": run_id,
        "status": run.get("status"),
        "input_count": run.get("input_count"),
        "output_count": run.get("output_count"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "lines": [_event_line(event) for event in events],
    }


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
