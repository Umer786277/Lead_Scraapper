"""
FastAPI entry point.

Run locally:
    uvicorn api.main:app --reload --port 8000

Production (Render):
    uvicorn api.main:app --host 0.0.0.0 --port $PORT
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

# Make the project root importable (db.py, pipeline.py, etc. live there)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Playwright browser path ───────────────────────────────────
# Store browsers inside the project directory so they persist on Render.
# Must be set before any Playwright import.
_PLAYWRIGHT_DIR = Path(__file__).resolve().parent.parent / ".playwright"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PLAYWRIGHT_DIR))

import db
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import analytics, calls, leads, outreach, pipeline, system

log = logging.getLogger("startup")

app = FastAPI(
    title="Lead Scraper API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ─────────────────────────────────────────────────────
_extra = [o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()]
_origins = list({
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://192.168.100.164:3000",
    *_extra,
})
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_origin_regex=r"https://.*\.onrender\.com",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────
app.include_router(leads.router,     prefix="/api/leads",     tags=["leads"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])
app.include_router(pipeline.router,  prefix="/api/pipeline",  tags=["pipeline"])
app.include_router(outreach.router,  prefix="/api/outreach",  tags=["outreach"])
app.include_router(system.router,    prefix="/api/system",    tags=["system"])
app.include_router(calls.router,     prefix="/api/calls",     tags=["calls"])


def _ensure_playwright():
    """Install Chromium browser binary if not present. Runs once at startup."""
    browsers_dir = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    existing = list(browsers_dir.glob("chromium*/chrome-linux64/chrome")) + \
               list(browsers_dir.glob("chromium*/chrome-headless-shell-linux64/chrome-headless-shell"))
    if existing:
        log.info(f"Playwright Chromium found at {existing[0]}")
        return
    log.info(f"Playwright Chromium not found in {browsers_dir} — installing now...")
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        log.info("Playwright Chromium installed successfully")
    else:
        log.error(f"Playwright install failed:\n{result.stderr[:500]}")


# ── Bootstrap ─────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    try:
        db.init_db()
    except Exception as e:
        log.error(f"DB init failed (API still starting): {e}")
    import threading
    from api import worker_runner
    # Run both in background so FastAPI responds immediately and passes Render's health check
    threading.Thread(target=_ensure_playwright, daemon=True).start()
    threading.Thread(target=worker_runner.start, daemon=True).start()


@app.on_event("shutdown")
def shutdown():
    from api import worker_runner
    worker_runner.stop()


@app.api_route("/", methods=["GET", "HEAD"], tags=["health"])
def health():
    return {"status": "ok", "version": "1.0.0"}
