"""
System monitoring — worker health, queue depth, and automation status.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import db

st.set_page_config(page_title="System", page_icon="⚙️", layout="wide")
db.init_db()

STATE_FILE = Path(__file__).parent.parent / "worker_state.json"

# ── Styling ─────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; max-width: 1200px; }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #1a1d2e 0%, #111321 100%); }

    .hero {
        background: linear-gradient(135deg, #0f2027 0%, #203a43 50%, #2c5364 100%);
        border-radius: 18px;
        padding: 1.75rem 2.25rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 10px 40px rgba(0,0,0,0.3);
    }
    .hero h1 { color: #fff; margin: 0; font-size: 1.9rem; }
    .hero p  { color: rgba(255,255,255,0.75); margin: 0.4rem 0 0 0; }

    .status-ok   { color: #4ade80; font-weight: 700; }
    .status-warn { color: #facc15; font-weight: 700; }
    .status-err  { color: #f87171; font-weight: 700; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <h1>⚙️  System</h1>
    <p>Worker health, send queue depth, and automation pipeline status.</p>
</div>
""", unsafe_allow_html=True)

# ── Auto-refresh toggle ──────────────────────────────────────
col_hdr, col_refresh = st.columns([4, 1])
with col_refresh:
    auto_refresh = st.toggle("Auto-refresh (30 s)", value=False)

# ── Load state ───────────────────────────────────────────────
state: dict = {}
if STATE_FILE.exists():
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        st.warning(f"Could not read worker_state.json: {e}")


# ── Worker status ────────────────────────────────────────────
st.subheader("Worker status")

if not state:
    st.error(
        "**Worker is not running.**\n\n"
        "Open a terminal in the project folder and run:\n"
        "```\npip install apscheduler\npython worker.py\n```"
    )
else:
    # Determine liveness: enrich runs every 30 s — if last run > 2 min ago, stalled
    enrich_job = state.get("jobs", {}).get("enrich", {})
    last_at    = enrich_job.get("at")
    is_live    = False

    if last_at:
        try:
            last_dt  = datetime.fromisoformat(last_at)
            elapsed  = (datetime.utcnow() - last_dt).total_seconds()
            is_live  = elapsed < 120
        except Exception:
            pass

    stopped_at = state.get("stopped_at")

    if stopped_at:
        st.warning(f"Worker stopped at **{stopped_at}**. Restart it with `python worker.py`.")
    elif is_live:
        st.success(
            f"Worker is **running** — PID {state.get('pid')}  ·  "
            f"started {state.get('started_at')}  ·  "
            f"auto campaign #{state.get('auto_campaign_id')}"
        )
    else:
        st.warning(
            "Worker **may be stalled** — last activity was more than 2 minutes ago. "
            "Check the terminal or restart with `python worker.py`."
        )

    # ── Job metrics ─────────────────────────────────────────
    st.divider()
    st.subheader("Last job results")

    jobs = state.get("jobs", {})

    c1, c2, c3 = st.columns(3)

    with c1:
        with st.container(border=True):
            st.markdown("##### 🔍 Enrich (every 30 s)")
            e = jobs.get("enrich", {})
            st.caption(f"Last run: {e.get('at', '—')}")
            if e:
                st.metric("Domains found",  e.get("found", 0))
                st.metric("Emails extracted", e.get("enriched", 0))
                st.metric("Leads enrolled",  e.get("enrolled", 0))
            if e.get("error"):
                st.error(e["error"])

    with c2:
        with st.container(border=True):
            st.markdown("##### 📤 Send queue (every 60 s)")
            s = jobs.get("send", {})
            st.caption(f"Last run: {s.get('at', '—')}")
            if s:
                st.metric("Sent",    s.get("sent", 0))
                st.metric("Failed",  s.get("failed", 0))
                st.metric("Skipped", s.get("skipped", 0))
            if s.get("error"):
                st.warning(s["error"])

    with c3:
        with st.container(border=True):
            st.markdown("##### 📥 Inbox (every 10 min)")
            i = jobs.get("inbox", {})
            st.caption(f"Last run: {i.get('at', '—')}")
            if i:
                st.metric("Replies matched", i.get("replies", 0))
            if i.get("error"):
                st.warning(i["error"])

# ── Queue depth ──────────────────────────────────────────────
st.divider()
st.subheader("Send queue depth")

try:
    q = db.send_queue_summary()
    if not any(q.values()):
        st.info("Queue is empty — no scheduled sends yet.")
    else:
        qc1, qc2, qc3, qc4 = st.columns(4)
        qc1.metric("Pending",   q.get("pending",   0))
        qc2.metric("Sent",      q.get("sent",      0))
        qc3.metric("Skipped",   q.get("skipped",   0))
        qc4.metric("Failed",    q.get("failed",    0))
except Exception as e:
    st.warning(f"Could not fetch queue stats: {e}")

# ── Pending enrichment ───────────────────────────────────────
st.divider()
st.subheader("Leads awaiting enrichment")

try:
    with db.get_conn() as c:
        pending_enrich = c.execute(
            """
            SELECT COUNT(DISTINCT domain) AS pending_domains,
                   COUNT(*) AS pending_leads
            FROM leads
            WHERE status = 'new'
              AND domain IS NOT NULL AND domain != ''
              AND (email IS NULL OR email = '')
              AND NOT EXISTS (SELECT 1 FROM domains d WHERE d.domain = leads.domain)
            """
        ).fetchone()

    if pending_enrich:
        pe1, pe2 = st.columns(2)
        pe1.metric("Unique domains to enrich", pending_enrich["pending_domains"])
        pe2.metric("Leads without email",       pending_enrich["pending_leads"])
        if pending_enrich["pending_domains"] > 0 and not state:
            st.info("Start the worker to automatically extract emails for these leads.")
except Exception as e:
    st.warning(f"Could not fetch enrichment stats: {e}")

# ── Pipeline overview ────────────────────────────────────────
st.divider()
st.subheader("Pipeline overview")

try:
    with db.get_conn() as c:
        lead_statuses = c.execute(
            "SELECT status, COUNT(*) AS n FROM leads GROUP BY status ORDER BY n DESC"
        ).fetchall()

    if lead_statuses:
        df = pd.DataFrame([dict(r) for r in lead_statuses])
        df.columns = ["Status", "Leads"]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("_No leads yet._")
except Exception as e:
    st.warning(f"Could not fetch lead statuses: {e}")

# ── Next scheduled sends ─────────────────────────────────────
st.divider()
st.subheader("Upcoming sends")

try:
    with db.get_conn() as c:
        upcoming = c.execute(
            """
            SELECT s.scheduled_at, l.business_name, l.email,
                   t.name AS template, s.step_number, s.status
            FROM scheduled_sends s
            JOIN leads l           ON l.id = s.lead_id
            JOIN campaign_steps cs ON cs.id = s.step_id
            JOIN email_templates t ON t.id = cs.template_id
            WHERE s.status = 'pending'
            ORDER BY s.scheduled_at ASC
            LIMIT 20
            """,
        ).fetchall()

    if upcoming:
        df = pd.DataFrame([dict(r) for r in upcoming])
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.caption("_No pending sends._")
except Exception as e:
    st.warning(f"Could not fetch upcoming sends: {e}")

# ── Configuration checklist ──────────────────────────────────
st.divider()
st.subheader("Configuration checklist")

checks = [
    ("DATABASE_URL",   os.getenv("DATABASE_URL"),   "PostgreSQL / Supabase connection string"),
    ("SMTP_HOST",      os.getenv("SMTP_HOST"),       "SMTP server (e.g. smtp.gmail.com)"),
    ("SMTP_USER",      os.getenv("SMTP_USER"),       "SMTP login / from address"),
    ("SMTP_PASSWORD",  os.getenv("SMTP_PASSWORD"),   "SMTP password / app password"),
    ("IMAP_HOST",      os.getenv("IMAP_HOST"),       "IMAP server for reply detection (optional)"),
    ("OPENAI_API_KEY", os.getenv("OPENAI_API_KEY"),  "OpenAI key for negotiation drafts (optional)"),
]

for key, val, desc in checks:
    if val:
        st.success(f"✅ **{key}** — {desc}")
    else:
        icon = "⚠️" if key.startswith("IMAP") or key == "OPENAI_API_KEY" else "❌"
        st.warning(f"{icon} **{key}** not set — {desc}")

# ── Auto-refresh ─────────────────────────────────────────────
if auto_refresh:
    time.sleep(30)
    st.rerun()
