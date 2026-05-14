"""
FastAPI entry point.

Run locally:
    uvicorn api.main:app --reload --port 8000

Production (Railway):
    uvicorn api.main:app --host 0.0.0.0 --port $PORT
"""

import os
import sys
from pathlib import Path

# Make the project root importable (db.py, pipeline.py, etc. live there)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import analytics, leads, outreach, pipeline, system

app = FastAPI(
    title="Lead Scraper API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ─────────────────────────────────────────────────────
# Allow the Next.js dev server (both localhost and LAN IP) plus production URL.
# Set CORS_ORIGINS=https://your-app.vercel.app in production to restrict this.
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
    allow_origin_regex=r"https://.*\.onrender\.com",  # all Render preview URLs
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


# ── Bootstrap ─────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    db.init_db()


@app.get("/", tags=["health"])
def health():
    return {"status": "ok", "version": "1.0.0"}
