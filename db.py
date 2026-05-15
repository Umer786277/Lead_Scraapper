"""
PostgreSQL storage layer (Supabase-compatible).

Set DATABASE_URL in .env. NEVER commit it — it contains credentials.

    DATABASE_URL=postgresql://user:password@host:5432/postgres

Uses psycopg v3 with dict-row factory. Compatible with Supabase's
transaction pooler (prepare_threshold=None disables prepared statements
which pgbouncer transaction mode doesn't support).
"""

import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load_env_file(path):
    """Minimal .env loader with zero dependencies. Doesn't overwrite existing env vars.

    Handles: `KEY=value`, `KEY="value"`, `KEY='value'`, blank lines, # comments.
    Uses utf-8-sig so a leading BOM won't eat the first key.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path(__file__).resolve().parent / ".env")

import psycopg
from psycopg import errors as pg_errors
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS extraction_runs (
    id            SERIAL PRIMARY KEY,
    run_type      TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,
    input_count   INTEGER DEFAULT 0,
    output_count  INTEGER DEFAULT 0,
    metadata      TEXT
);

CREATE TABLE IF NOT EXISTS domains (
    id             SERIAL PRIMARY KEY,
    run_id         INTEGER REFERENCES extraction_runs(id),
    domain         TEXT NOT NULL,
    status         TEXT NOT NULL,
    has_mx         INTEGER,
    pages_visited  INTEGER DEFAULT 0,
    emails_found   INTEGER DEFAULT 0,
    error          TEXT,
    scraped_at     TEXT
);

CREATE TABLE IF NOT EXISTS emails (
    id                  SERIAL PRIMARY KEY,
    domain_id           INTEGER REFERENCES domains(id),
    domain              TEXT NOT NULL,
    email               TEXT NOT NULL,
    source_url          TEXT,
    source_type         TEXT,
    confidence          TEXT,
    is_role             INTEGER DEFAULT 0,
    category            TEXT,
    verification_status TEXT,
    extracted_at        TEXT,
    UNIQUE(domain, email)
);

CREATE TABLE IF NOT EXISTS leads (
    id             SERIAL PRIMARY KEY,
    source         TEXT NOT NULL,
    run_id         INTEGER,
    business_name  TEXT,
    domain         TEXT,
    email          TEXT,
    phone          TEXT,
    website        TEXT,
    address        TEXT,
    city           TEXT,
    country        TEXT,
    rating         REAL,
    reviews        INTEGER,
    status            TEXT DEFAULT 'new',
    notes             TEXT,
    homepage_snippet  TEXT,
    opener            TEXT,
    contact_name      TEXT,
    maps_url          TEXT,
    improvement_note  TEXT,
    last_review_days  INTEGER,
    created_at        TEXT
);

CREATE TABLE IF NOT EXISTS email_templates (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    leads_count  INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS campaign_steps (
    id            SERIAL PRIMARY KEY,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns(id),
    step_number   INTEGER NOT NULL,
    delay_days    INTEGER NOT NULL,
    template_id   INTEGER NOT NULL REFERENCES email_templates(id)
);

CREATE TABLE IF NOT EXISTS scheduled_sends (
    id             SERIAL PRIMARY KEY,
    campaign_id    INTEGER NOT NULL REFERENCES campaigns(id),
    step_id        INTEGER NOT NULL REFERENCES campaign_steps(id),
    step_number    INTEGER NOT NULL,
    lead_id        INTEGER NOT NULL REFERENCES leads(id),
    scheduled_at   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    sent_at        TEXT,
    error          TEXT
);

CREATE TABLE IF NOT EXISTS email_events (
    id            SERIAL PRIMARY KEY,
    lead_id       INTEGER NOT NULL REFERENCES leads(id),
    campaign_id   INTEGER REFERENCES campaigns(id),
    event_type    TEXT NOT NULL,
    subject       TEXT,
    message_id    TEXT,
    note          TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vendor_threads (
    id                SERIAL PRIMARY KEY,
    lead_id           INTEGER NOT NULL UNIQUE REFERENCES leads(id),
    subject           TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    price_floor       REAL,
    currency          TEXT DEFAULT 'USD',
    goal              TEXT,
    last_inbound_at   TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS negotiation_messages (
    id            SERIAL PRIMARY KEY,
    thread_id     INTEGER NOT NULL REFERENCES vendor_threads(id),
    direction     TEXT NOT NULL,
    subject       TEXT,
    body          TEXT,
    message_id    TEXT UNIQUE,
    in_reply_to   TEXT,
    from_addr     TEXT,
    to_addr       TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS negotiation_drafts (
    id                 SERIAL PRIMARY KEY,
    thread_id          INTEGER NOT NULL REFERENCES vendor_threads(id),
    model              TEXT,
    draft_subject      TEXT,
    draft_body         TEXT,
    detected_price     REAL,
    detected_currency  TEXT,
    reasoning          TEXT,
    suggested_action   TEXT,
    status             TEXT NOT NULL DEFAULT 'pending',
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id            SERIAL PRIMARY KEY,
    lead_id       INTEGER NOT NULL REFERENCES leads(id),
    thread_id     INTEGER REFERENCES vendor_threads(id),
    vendor_name   TEXT,
    final_price   REAL,
    currency      TEXT,
    terms         TEXT,
    notes         TEXT,
    closed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    pid        INTEGER,
    started_at TEXT,
    updated_at TEXT,
    jobs       TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS calls (
    id              SERIAL PRIMARY KEY,
    lead_id         INTEGER NOT NULL REFERENCES leads(id),
    vapi_call_id    TEXT UNIQUE,
    status          TEXT NOT NULL DEFAULT 'queued',
    duration_sec    INTEGER,
    transcript      TEXT,
    summary         TEXT,
    recording_url   TEXT,
    qualified       TEXT,
    notes           TEXT,
    ended_reason    TEXT,
    scheduled_at    TEXT NOT NULL,
    initiated_at    TEXT,
    ended_at        TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calls_lead         ON calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_calls_status       ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_vapi         ON calls(vapi_call_id);
CREATE INDEX IF NOT EXISTS idx_emails_domain      ON emails(domain);
CREATE INDEX IF NOT EXISTS idx_leads_source       ON leads(source);
CREATE INDEX IF NOT EXISTS idx_leads_status       ON leads(status);
CREATE INDEX IF NOT EXISTS idx_domains_run        ON domains(run_id);
CREATE INDEX IF NOT EXISTS idx_sched_status       ON scheduled_sends(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_sched_lead         ON scheduled_sends(lead_id);
CREATE INDEX IF NOT EXISTS idx_events_lead        ON email_events(lead_id);
CREATE INDEX IF NOT EXISTS idx_threads_lead       ON vendor_threads(lead_id);
CREATE INDEX IF NOT EXISTS idx_nmsgs_thread       ON negotiation_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_drafts_thread      ON negotiation_drafts(thread_id, status);
"""


_MIGRATIONS = [
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS maps_url TEXT",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS improvement_note TEXT",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_review_days INTEGER",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS reviews INTEGER",
]


def init_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env:\n"
            "  DATABASE_URL=postgresql://user:pass@host:5432/postgres"
        )
    # psycopg uses extended-query mode, which can't handle multi-statement strings.
    # Split on semicolons and run each CREATE separately.
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    with get_conn() as c:
        with c.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
            # Additive migrations — safe to re-run (IF NOT EXISTS)
            for stmt in _MIGRATIONS:
                cur.execute(stmt)


@contextmanager
def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        # Supabase transaction pooler (port 5432) doesn't support prepared statements
        prepare_threshold=None,
        autocommit=False,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _scalar(cur):
    """Fetch first column of first row — used for COUNT(*) etc."""
    row = cur.fetchone()
    if not row:
        return 0
    return next(iter(row.values()))


# ── Runs ─────────────────────────────────────────────────────
def start_run(run_type, input_count=0, metadata=None):
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO extraction_runs (run_type, started_at, status, input_count, metadata) "
            "VALUES (%s, %s, 'running', %s, %s) RETURNING id",
            (run_type, now(), input_count, json.dumps(metadata or {})),
        )
        return cur.fetchone()["id"]


def finish_run(run_id, output_count, status="completed"):
    with get_conn() as c:
        c.execute(
            "UPDATE extraction_runs SET finished_at=%s, status=%s, output_count=%s WHERE id=%s",
            (now(), status, output_count, run_id),
        )


# ── Domains ──────────────────────────────────────────────────
def insert_domain(run_id, domain, status, has_mx, pages_visited, emails_found, error=None):
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO domains "
            "(run_id, domain, status, has_mx, pages_visited, emails_found, error, scraped_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (run_id, domain, status, int(bool(has_mx)), pages_visited, emails_found, error, now()),
        )
        return cur.fetchone()["id"]


# ── Emails ───────────────────────────────────────────────────
def insert_email(domain_id, domain, email, source_url, source_type, confidence,
                 is_role, has_mx, category=None):
    with get_conn() as c:
        try:
            c.execute(
                "INSERT INTO emails "
                "(domain_id, domain, email, source_url, source_type, confidence, "
                " is_role, category, verification_status, extracted_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    domain_id, domain, email, source_url, source_type, confidence,
                    int(bool(is_role)),
                    category,
                    "valid_mx" if has_mx else "no_mx",
                    now(),
                ),
            )
        except pg_errors.UniqueViolation:
            pass


# ── Leads ────────────────────────────────────────────────────
def insert_lead(**kwargs):
    kwargs.setdefault("created_at", now())
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("%s" for _ in kwargs)
    with get_conn() as c:
        cur = c.execute(
            f"INSERT INTO leads ({cols}) VALUES ({placeholders}) RETURNING id",
            tuple(kwargs.values()),
        )
        return cur.fetchone()["id"]


# ── Analytics helpers ────────────────────────────────────────
def overview_stats():
    with get_conn() as c:
        def one(q):
            return _scalar(c.execute(q))
        return {
            "leads_total":       one("SELECT COUNT(*) FROM leads"),
            "emails_total":      one("SELECT COUNT(*) FROM emails"),
            "domains_total":     one("SELECT COUNT(*) FROM domains"),
            "runs_total":        one("SELECT COUNT(*) FROM extraction_runs"),
            "emails_high_conf":  one("SELECT COUNT(*) FROM emails WHERE confidence='high'"),
            "emails_with_mx":    one("SELECT COUNT(*) FROM emails WHERE verification_status='valid_mx'"),
        }


def runs_summary(limit=20):
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, run_type, started_at, finished_at, status, input_count, output_count "
            "FROM extraction_runs ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def emails_by_domain(limit=50):
    with get_conn() as c:
        rows = c.execute(
            "SELECT domain, COUNT(*) AS email_count, "
            "SUM(CASE WHEN confidence='high' THEN 1 ELSE 0 END) AS high_conf "
            "FROM emails GROUP BY domain ORDER BY email_count DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def recent_emails(limit=100):
    with get_conn() as c:
        rows = c.execute(
            "SELECT domain, email, source_type, confidence, is_role, "
            "verification_status, extracted_at "
            "FROM emails ORDER BY id DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def leads_by_status():
    with get_conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM leads GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def emails_by_day(days=14):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    with get_conn() as c:
        rows = c.execute(
            "SELECT SUBSTR(extracted_at, 1, 10) AS day, COUNT(*) AS n "
            "FROM emails WHERE extracted_at >= %s "
            "GROUP BY day ORDER BY day",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Templates ────────────────────────────────────────────────
def upsert_template(name, subject, body, template_id=None):
    ts = now()
    with get_conn() as c:
        if template_id:
            c.execute(
                "UPDATE email_templates SET name=%s, subject=%s, body=%s, updated_at=%s WHERE id=%s",
                (name, subject, body, ts, template_id),
            )
            return template_id
        cur = c.execute(
            "INSERT INTO email_templates (name, subject, body, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (name, subject, body, ts, ts),
        )
        return cur.fetchone()["id"]


def list_templates():
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM email_templates ORDER BY updated_at DESC"
        ).fetchall()]


def get_template(template_id):
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM email_templates WHERE id=%s", (template_id,)
        ).fetchone()
        return dict(row) if row else None


def delete_template(template_id):
    with get_conn() as c:
        c.execute("DELETE FROM email_templates WHERE id=%s", (template_id,))


# ── Leads (for selection) ────────────────────────────────────
def sendable_leads(source=None, status="new", limit=500):
    q = ("SELECT id, source, business_name, domain, email, website, status, created_at "
         "FROM leads WHERE email IS NOT NULL AND email != '' AND status=%s")
    args = [status]
    if source:
        q += " AND source=%s"
        args.append(source)
    q += " ORDER BY id DESC LIMIT %s"
    args.append(limit)
    with get_conn() as c:
        return [dict(r) for r in c.execute(q, tuple(args)).fetchall()]


def get_lead(lead_id):
    with get_conn() as c:
        row = c.execute("SELECT * FROM leads WHERE id=%s", (lead_id,)).fetchone()
        return dict(row) if row else None


def update_lead_status(lead_id, status, note=None):
    with get_conn() as c:
        c.execute("UPDATE leads SET status=%s WHERE id=%s", (status, lead_id))
        if note is not None:
            c.execute(
                "UPDATE leads SET notes=COALESCE(notes,'') || %s WHERE id=%s",
                (f"\n[{now()}] {note}", lead_id),
            )


# ── Campaigns ────────────────────────────────────────────────
def create_campaign(name, steps, lead_ids, notes=None):
    base = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO campaigns (name, status, leads_count, created_at, notes) "
            "VALUES (%s, 'active', %s, %s, %s) RETURNING id",
            (name, len(lead_ids), now(), notes),
        )
        campaign_id = cur.fetchone()["id"]

        step_ids = []
        for i, s in enumerate(steps, 1):
            cur = c.execute(
                "INSERT INTO campaign_steps "
                "(campaign_id, step_number, delay_days, template_id) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (campaign_id, i, s["delay_days"], s["template_id"]),
            )
            step_ids.append((cur.fetchone()["id"], i, s["delay_days"]))

        for lead_id in lead_ids:
            cumulative = 0
            for step_id, step_num, delay in step_ids:
                cumulative += delay
                scheduled = (base + timedelta(days=cumulative)).isoformat(timespec="seconds")
                c.execute(
                    "INSERT INTO scheduled_sends "
                    "(campaign_id, step_id, step_number, lead_id, scheduled_at, status) "
                    "VALUES (%s, %s, %s, %s, %s, 'pending')",
                    (campaign_id, step_id, step_num, lead_id, scheduled),
                )
        return campaign_id


def list_campaigns():
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM scheduled_sends s WHERE s.campaign_id=c.id AND s.status='sent') AS sent_count, "
            "(SELECT COUNT(*) FROM scheduled_sends s WHERE s.campaign_id=c.id AND s.status='pending') AS pending_count "
            "FROM campaigns c ORDER BY c.id DESC"
        ).fetchall()]


def set_campaign_status(campaign_id, status):
    with get_conn() as c:
        c.execute("UPDATE campaigns SET status=%s WHERE id=%s", (status, campaign_id))


def campaign_steps(campaign_id):
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT cs.*, t.name AS template_name, t.subject, t.body "
            "FROM campaign_steps cs "
            "JOIN email_templates t ON t.id = cs.template_id "
            "WHERE cs.campaign_id=%s ORDER BY cs.step_number",
            (campaign_id,)
        ).fetchall()]


# ── Scheduled sends ──────────────────────────────────────────
def due_sends(limit=50):
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.id AS send_id, s.campaign_id, s.step_id, s.step_number, "
            "       s.lead_id, s.scheduled_at, "
            "       l.email, l.business_name, l.domain, l.status AS lead_status, "
            "       t.subject, t.body, t.name AS template_name, "
            "       c.name AS campaign_name, c.status AS campaign_status "
            "FROM scheduled_sends s "
            "JOIN leads l            ON l.id = s.lead_id "
            "JOIN campaign_steps cs  ON cs.id = s.step_id "
            "JOIN email_templates t  ON t.id = cs.template_id "
            "JOIN campaigns c        ON c.id = s.campaign_id "
            "WHERE s.status='pending' "
            "  AND s.scheduled_at <= %s "
            "  AND c.status='active' "
            "ORDER BY s.scheduled_at ASC LIMIT %s",
            (now(), limit),
        ).fetchall()]


def mark_send(send_id, status, error=None):
    with get_conn() as c:
        c.execute(
            "UPDATE scheduled_sends SET status=%s, sent_at=%s, error=%s WHERE id=%s",
            (status, now() if status == "sent" else None, error, send_id),
        )


def reschedule_send(send_id, scheduled_at):
    """Defer a pending send by overwriting its scheduled_at. Status stays
    'pending' so it gets re-picked up at the new time. Used by the
    business-hours gate to push outside-hours sends into the next 9am window.
    """
    with get_conn() as c:
        c.execute(
            "UPDATE scheduled_sends SET scheduled_at=%s WHERE id=%s",
            (scheduled_at, send_id),
        )


def cancel_future_sends_for_lead(lead_id):
    with get_conn() as c:
        c.execute(
            "UPDATE scheduled_sends SET status='cancelled' "
            "WHERE lead_id=%s AND status='pending'",
            (lead_id,),
        )


def send_queue_summary():
    with get_conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM scheduled_sends GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


# ── Calls ────────────────────────────────────────────────────
def queue_call(lead_id: int) -> int:
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO calls (lead_id, status, scheduled_at, created_at) "
            "VALUES (%s, 'queued', %s, %s) RETURNING id",
            (lead_id, now(), now()),
        )
        return cur.fetchone()["id"]


def get_queued_calls(limit: int = 5) -> list:
    with get_conn() as c:
        rows = c.execute(
            """SELECT c.id, c.lead_id, c.scheduled_at,
                      l.business_name, l.phone, l.city, l.country, l.website
               FROM calls c JOIN leads l ON l.id = c.lead_id
               WHERE c.status = 'queued' AND c.scheduled_at <= %s
               ORDER BY c.scheduled_at ASC LIMIT %s""",
            (now(), limit),
        ).fetchall()
        return [dict(r) for r in rows]


def update_call(call_id: int, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=%s" for k in fields)
    with get_conn() as c:
        c.execute(f"UPDATE calls SET {cols} WHERE id=%s", (*fields.values(), call_id))


def update_call_by_vapi_id(vapi_call_id: str, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=%s" for k in fields)
    with get_conn() as c:
        c.execute(f"UPDATE calls SET {cols} WHERE vapi_call_id=%s", (*fields.values(), vapi_call_id))


def get_call_lead_id(vapi_call_id: str):
    with get_conn() as c:
        row = c.execute(
            "SELECT lead_id FROM calls WHERE vapi_call_id=%s", (vapi_call_id,)
        ).fetchone()
        return row["lead_id"] if row else None


def list_calls(limit: int = 100, offset: int = 0) -> list:
    with get_conn() as c:
        rows = c.execute(
            """SELECT c.*, l.business_name, l.phone, l.city, l.country, l.rating, l.reviews
               FROM calls c JOIN leads l ON l.id = c.lead_id
               ORDER BY c.id DESC LIMIT %s OFFSET %s""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def calls_summary() -> dict:
    with get_conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM calls GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def worker_heartbeat_write(pid: int, started_at: str, jobs: dict):
    """Upsert worker state into DB so the API can read it cross-container."""
    with get_conn() as c:
        c.execute(
            """INSERT INTO worker_heartbeat (id, pid, started_at, updated_at, jobs)
               VALUES (1, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE
               SET pid=%s, started_at=%s, updated_at=%s, jobs=%s""",
            (pid, started_at, now(), json.dumps(jobs),
             pid, started_at, now(), json.dumps(jobs)),
        )


def worker_heartbeat_read() -> dict:
    """Read worker state from DB. Returns {} if no heartbeat recorded yet."""
    try:
        with get_conn() as c:
            row = c.execute(
                "SELECT pid, started_at, updated_at, jobs FROM worker_heartbeat WHERE id=1"
            ).fetchone()
            if not row:
                return {}
            result = dict(row)
            result["jobs"] = json.loads(result.get("jobs") or "{}")
            return result
    except Exception:
        return {}


# ── Events ───────────────────────────────────────────────────
def log_event(lead_id, event_type, campaign_id=None, subject=None, message_id=None, note=None):
    with get_conn() as c:
        c.execute(
            "INSERT INTO email_events "
            "(lead_id, campaign_id, event_type, subject, message_id, note, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (lead_id, campaign_id, event_type, subject, message_id, note, now()),
        )


def events_for_lead(lead_id):
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM email_events WHERE lead_id=%s ORDER BY created_at DESC",
            (lead_id,)
        ).fetchall()]


# ── Negotiation: threads, messages, drafts ───────────────────
def get_or_create_thread(lead_id, subject=None):
    with get_conn() as c:
        row = c.execute(
            "SELECT id FROM vendor_threads WHERE lead_id=%s", (lead_id,)
        ).fetchone()
        if row:
            return row["id"]
        cur = c.execute(
            "INSERT INTO vendor_threads (lead_id, subject, status, created_at) "
            "VALUES (%s, %s, 'active', %s) RETURNING id",
            (lead_id, subject, now()),
        )
        return cur.fetchone()["id"]


def update_thread(thread_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=%s" for k in fields)
    with get_conn() as c:
        c.execute(f"UPDATE vendor_threads SET {cols} WHERE id=%s",
                  (*fields.values(), thread_id))


def get_thread(thread_id):
    with get_conn() as c:
        row = c.execute(
            "SELECT vt.*, l.business_name, l.email, l.domain "
            "FROM vendor_threads vt JOIN leads l ON l.id = vt.lead_id "
            "WHERE vt.id=%s", (thread_id,),
        ).fetchone()
        return dict(row) if row else None


def list_threads():
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT vt.*, l.business_name, l.email, l.domain, "
            "  (SELECT COUNT(*) FROM negotiation_messages m "
            "   WHERE m.thread_id=vt.id AND m.direction='inbound') AS inbound_count, "
            "  (SELECT COUNT(*) FROM negotiation_messages m "
            "   WHERE m.thread_id=vt.id) AS total_msgs, "
            "  (SELECT COUNT(*) FROM negotiation_drafts d "
            "   WHERE d.thread_id=vt.id AND d.status='pending') AS pending_drafts "
            "FROM vendor_threads vt JOIN leads l ON l.id = vt.lead_id "
            "ORDER BY COALESCE(vt.last_inbound_at, vt.created_at) DESC"
        ).fetchall()]


def insert_thread_message(thread_id, direction, subject, body,
                          message_id=None, in_reply_to=None,
                          from_addr=None, to_addr=None):
    with get_conn() as c:
        try:
            c.execute(
                "INSERT INTO negotiation_messages "
                "(thread_id, direction, subject, body, message_id, in_reply_to, "
                " from_addr, to_addr, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (thread_id, direction, subject, body, message_id, in_reply_to,
                 from_addr, to_addr, now()),
            )
        except pg_errors.UniqueViolation:
            return
        if direction == "inbound":
            c.execute(
                "UPDATE vendor_threads SET last_inbound_at=%s WHERE id=%s",
                (now(), thread_id),
            )


def thread_messages(thread_id):
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM negotiation_messages WHERE thread_id=%s ORDER BY created_at ASC",
            (thread_id,)
        ).fetchall()]


def insert_draft(thread_id, model, draft_subject, draft_body,
                 detected_price=None, detected_currency=None,
                 reasoning=None, suggested_action=None):
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO negotiation_drafts "
            "(thread_id, model, draft_subject, draft_body, detected_price, "
            " detected_currency, reasoning, suggested_action, status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s) RETURNING id",
            (thread_id, model, draft_subject, draft_body, detected_price,
             detected_currency, reasoning, suggested_action, now()),
        )
        return cur.fetchone()["id"]


def get_draft(draft_id):
    with get_conn() as c:
        row = c.execute("SELECT * FROM negotiation_drafts WHERE id=%s", (draft_id,)).fetchone()
        return dict(row) if row else None


def pending_draft_for_thread(thread_id):
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM negotiation_drafts "
            "WHERE thread_id=%s AND status='pending' "
            "ORDER BY id DESC LIMIT 1",
            (thread_id,)
        ).fetchone()
        return dict(row) if row else None


def update_draft(draft_id, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=%s" for k in fields)
    with get_conn() as c:
        c.execute(f"UPDATE negotiation_drafts SET {cols} WHERE id=%s",
                  (*fields.values(), draft_id))


def create_deal(lead_id, thread_id, vendor_name, final_price, currency,
                terms=None, notes=None):
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO deals (lead_id, thread_id, vendor_name, final_price, "
            "currency, terms, notes, closed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (lead_id, thread_id, vendor_name, final_price, currency, terms, notes, now()),
        )
        return cur.fetchone()["id"]


def list_deals():
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT d.*, l.business_name, l.email AS vendor_email "
            "FROM deals d JOIN leads l ON l.id = d.lead_id "
            "ORDER BY d.closed_at DESC"
        ).fetchall()]
