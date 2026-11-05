"""
FastAPI entry point.

Run locally:
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000

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

# Windows consoles default to the cp1252 codepage, which can't encode the
# emoji used in scraper log/print output and crashes the pipeline mid-run.
# Force UTF-8 so any print()/log line survives regardless of host console.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# ── Playwright browser path ───────────────────────────────────
# Store browsers inside the project directory so they persist on Render.
# Must be set before any Playwright import.
_PLAYWRIGHT_DIR = Path(__file__).resolve().parent.parent / ".playwright"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(_PLAYWRIGHT_DIR))

# ── Console logging ───────────────────────────────────────────
# worker_runner only attaches an in-memory buffer; without a stream handler
# the pipeline's INFO events never reach the uvicorn console.
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
if not any(
    isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
    for h in _root_logger.handlers
):
    _stream_handler = logging.StreamHandler()
    _stream_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    _root_logger.addHandler(_stream_handler)

import db
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import analytics, calls, leads, orgs, outreach, pipeline, system

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
    "http://192.168.1.28:3000",
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
app.include_router(orgs.router,      prefix="/api/orgs",      tags=["orgs"])


def _ensure_playwright():
    """Install Chromium browser binary if not present. Runs once at startup."""
    browsers_dir = Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])
    # INSTALLATION_COMPLETE exists on Windows, Linux and macOS installs alike.
    # Checking platform-specific executable names only broke on Windows, where
    # the old chrome-linux64 glob never matched and every startup tried to
    # reinstall Chromium inside the uvicorn-reload watched directory.
    existing = list(browsers_dir.glob("chromium*/INSTALLATION_COMPLETE"))
    if existing:
        log.info(f"Playwright Chromium found at {existing[0].parent}")
        return
    if "--reload" in sys.argv:
        log.warning(
            f"Playwright Chromium not found in {browsers_dir}. "
            "Skipping auto-install while uvicorn --reload is active; run "
            "'playwright install chromium' manually."
        )
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
    threading.Thread(target=_ensure_playwright, daemon=True).start()
    if os.getenv("RUN_WORKER_IN_API", "").strip().lower() in {"1", "true", "yes"}:
        from api import worker_runner
        threading.Thread(target=worker_runner.start, daemon=True).start()


@app.on_event("shutdown")
def shutdown():
    if os.getenv("RUN_WORKER_IN_API", "").strip().lower() in {"1", "true", "yes"}:
        from api import worker_runner
        worker_runner.stop()


@app.api_route("/", methods=["GET", "HEAD"], tags=["health"])
def health():
    return {"status": "ok", "version": "1.0.0"}
