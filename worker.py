"""
Automation worker — background process driving the email pipeline.

Jobs (all run independently via APScheduler):
  enrich  (every 30 s): new leads with domains → extract emails → enroll in auto campaign
  send    (every 60 s): process the outreach send queue via SMTP
  inbox   (every 10 min): IMAP poll for replies → cancel follow-ups

Run in a separate terminal alongside Streamlit:
    pip install apscheduler
    python worker.py

Status is written to worker_state.json and shown in the System page.
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Load .env BEFORE any module that calls os.getenv at import time —
# otherwise SMTP / IMAP / OPENAI env vars come back empty on first job tick.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Windows asyncio fix — must come before any Playwright import.
# Streamlit's Tornado forces WindowsSelectorEventLoopPolicy which can't
# spawn subprocesses. Force Proactor so Playwright works here too.
if sys.platform == "win32":
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from apscheduler.schedulers.blocking import BlockingScheduler

import db
from outreach import SMTPConfig, process_queue, seed_default_templates

# ── Config ───────────────────────────────────────────────────
ENRICH_BATCH        = 10    # max domains per enrich run
OUTREACH_DELAY_MIN  = 2     # minutes after email found → first send
FOLLOWUP_DELAYS_DAYS = [3, 7, 14]  # incremental delays between follow-ups
STATE_FILE          = Path(__file__).parent / "worker_state.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("worker")


# ── Helpers ──────────────────────────────────────────────────
def _now_str():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _write_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"Could not write state file: {e}")
    try:
        db.worker_heartbeat_write(
            pid=state.get("pid", os.getpid()),
            started_at=state.get("started_at", _now_str()),
            jobs=state.get("jobs", {}),
        )
    except Exception as e:
        log.warning(f"Could not write heartbeat to DB: {e}")


def _read_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


_CONF_RANK = {"high": 3, "medium": 2, "low": 1}
_CATEGORY_RANK = {
    "personal_named": 15,
    "personal":       8,
    "role":           0,
    "pattern_guess": -10,
}


def _email_score(e: dict) -> int:
    conf           = _CONF_RANK.get(e.get("confidence"), 0) * 10
    category_bonus = _CATEGORY_RANK.get(
        e.get("category"),
        -2 if e.get("is_role") else 0,
    )
    source_bonus   = 3 if e.get("source_type") == "mailto" else 0
    return conf + category_bonus + source_bonus


# ── Auto-campaign bootstrap ──────────────────────────────────
def _ensure_auto_campaign() -> tuple[int | None, list]:
    """
    Idempotently create the 'Auto Outreach' campaign with 4 steps using the
    default templates. Returns (campaign_id, steps_list). Each step dict has
    keys: step_id, step_number, delay_days, template_id.
    """
    seed_default_templates()
    templates = db.list_templates()
    if not templates:
        log.error("No email templates — cannot create auto campaign")
        return None, []

    with db.get_conn() as c:
        row = c.execute(
            "SELECT id FROM campaigns WHERE name='Auto Outreach' LIMIT 1"
        ).fetchone()

        if row:
            campaign_id = row["id"]
        else:
            cur = c.execute(
                "INSERT INTO campaigns (name, status, leads_count, created_at, notes) "
                "VALUES ('Auto Outreach', 'active', 0, %s, %s) RETURNING id",
                (db.now(), "Managed by worker.py — do not edit steps manually."),
            )
            campaign_id = cur.fetchone()["id"]
            log.info(f"Created auto campaign id={campaign_id}")

        # Ensure steps exist
        existing_steps = c.execute(
            "SELECT cs.id AS step_id, cs.step_number, cs.delay_days, cs.template_id "
            "FROM campaign_steps cs WHERE cs.campaign_id=%s ORDER BY cs.step_number",
            (campaign_id,),
        ).fetchall()

        if not existing_steps:
            # Step 1 has delay_days=0 (timing comes from OUTREACH_DELAY_MIN)
            # Steps 2-4 use FOLLOWUP_DELAYS_DAYS as incremental gaps
            delays = [0] + FOLLOWUP_DELAYS_DAYS
            step_records = []
            for i, (delay, tmpl) in enumerate(zip(delays, templates), 1):
                cur = c.execute(
                    "INSERT INTO campaign_steps "
                    "(campaign_id, step_number, delay_days, template_id) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (campaign_id, i, delay, tmpl["id"]),
                )
                step_records.append({
                    "step_id":     cur.fetchone()["id"],
                    "step_number": i,
                    "delay_days":  delay,
                    "template_id": tmpl["id"],
                })
            log.info(f"Created {len(step_records)} steps for auto campaign")
            return campaign_id, step_records

        return campaign_id, [dict(s) for s in existing_steps]


def _enroll_lead(lead_id: int, campaign_id: int, steps: list) -> bool:
    """
    Schedule all campaign steps for one lead. Step 1 fires in OUTREACH_DELAY_MIN
    minutes; subsequent steps add their delay_days on top of that anchor.
    Returns True if enrolled, False if skipped (already enrolled or ineligible).
    """
    anchor = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=OUTREACH_DELAY_MIN)

    with db.get_conn() as c:
        # Already enrolled?
        if c.execute(
            "SELECT 1 FROM scheduled_sends WHERE lead_id=%s AND campaign_id=%s LIMIT 1",
            (lead_id, campaign_id),
        ).fetchone():
            return False

        lead = c.execute(
            "SELECT email, status FROM leads WHERE id=%s", (lead_id,)
        ).fetchone()
        if not lead or not lead["email"]:
            return False
        if lead["status"] in ("replied", "converted", "dead", "contacted"):
            return False

        cumulative = timedelta(0)
        for step in steps:
            if step["step_number"] > 1:
                cumulative += timedelta(days=step["delay_days"])
            send_at = (anchor + cumulative).isoformat(timespec="seconds")
            c.execute(
                "INSERT INTO scheduled_sends "
                "(campaign_id, step_id, step_number, lead_id, scheduled_at, status) "
                "VALUES (%s, %s, %s, %s, %s, 'pending')",
                (campaign_id, step["step_id"], step["step_number"], lead_id, send_at),
            )
    return True


# ── AI improvement note ──────────────────────────────────────
def _generate_improvement_note(domain: str, snippet: str) -> str | None:
    """Generate a 1-2 sentence note about business problems we could help with."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or not snippet:
        return None
    try:
        import requests
        prompt = (
            f"Business website: {domain}\n"
            f"Homepage text: {snippet[:600]}\n\n"
            "In 1-2 sentences, identify the biggest improvement opportunity for this business "
            "(e.g. weak online presence, no reviews strategy, poor lead capture, slow follow-up, "
            "missing contact info). Be specific and actionable. Reply with just the note, no preamble."
        )
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 120,
                "temperature": 0.4,
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()[:500]
    except Exception as e:
        log.debug(f"AI note failed for {domain}: {e}")
        return None


# ── Jobs ─────────────────────────────────────────────────────
def job_enrich(state: dict, campaign_id: int | None, steps: list):
    """Find leads with domains but no email, extract, then enroll in auto campaign."""
    log.info("enrich: starting")
    result = {"at": _now_str(), "found": 0, "enriched": 0, "enrolled": 0, "error": None}

    try:
        with db.get_conn() as c:
            rows = c.execute(
                """
                SELECT l.domain,
                       ARRAY_AGG(l.id ORDER BY l.id) AS lead_ids
                FROM leads l
                WHERE l.status = 'new'
                  AND l.domain IS NOT NULL AND l.domain != ''
                  AND (l.email IS NULL OR l.email = '')
                  AND NOT EXISTS (
                      SELECT 1 FROM domains d WHERE d.domain = l.domain
                  )
                GROUP BY l.domain
                LIMIT %s
                """,
                (ENRICH_BATCH,),
            ).fetchall()

        result["found"] = len(rows)

        if not rows:
            log.info("enrich: nothing to do")
            state["jobs"]["enrich"] = result
            _write_state(state)
            return

        from playwright.sync_api import sync_playwright
        from email_extractor import extract_from_domain

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )

            def _block(route):
                if route.request.resource_type in ("image", "media", "font"):
                    return route.abort()
                return route.continue_()

            context.route("**/*", _block)

            for row in rows:
                domain   = row["domain"]
                lead_ids = row["lead_ids"]
                log.info(f"enrich: {domain} (leads: {lead_ids})")

                try:
                    extraction = extract_from_domain(domain, context)
                except Exception as e:
                    log.warning(f"enrich: {domain} failed — {e}")
                    # Still record the domain so we don't retry every 30 s
                    try:
                        db.insert_domain(
                            run_id=None, domain=domain, status="failed",
                            has_mx=None, pages_visited=0, emails_found=0,
                            error=str(e)[:200],
                        )
                    except Exception:
                        pass
                    continue

                status = (
                    "scraped" if not extraction.get("error")
                    else ("no_mx" if extraction.get("error") == "no_mx" else "failed")
                )
                domain_id = db.insert_domain(
                    run_id=None,
                    domain=domain,
                    status=status,
                    has_mx=extraction.get("has_mx"),
                    pages_visited=extraction.get("pages_visited", 0),
                    emails_found=len(extraction.get("emails", [])),
                    error=extraction.get("error"),
                )

                best_email = None
                for e in extraction.get("emails", []):
                    try:
                        db.insert_email(
                            domain_id=domain_id,
                            domain=domain,
                            email=e["email"],
                            source_url=e.get("source_url"),
                            source_type=e.get("source_type"),
                            confidence=e.get("confidence"),
                            is_role=e.get("is_role"),
                            has_mx=extraction.get("has_mx"),
                            category=e.get("category"),
                        )
                    except Exception:
                        pass
                    if best_email is None or _email_score(e) > _email_score(best_email):
                        best_email = e

                snippet = extraction.get("snippet")
                improvement_note = _generate_improvement_note(domain, snippet) if snippet else None

                if best_email or snippet:
                    with db.get_conn() as c:
                        for lid in lead_ids:
                            if best_email:
                                c.execute(
                                    "UPDATE leads SET email=%s "
                                    "WHERE id=%s AND (email IS NULL OR email='')",
                                    (best_email["email"], lid),
                                )
                            if snippet:
                                c.execute(
                                    "UPDATE leads SET homepage_snippet=%s "
                                    "WHERE id=%s AND (homepage_snippet IS NULL OR homepage_snippet='')",
                                    (snippet, lid),
                                )
                            if improvement_note:
                                c.execute(
                                    "UPDATE leads SET improvement_note=%s "
                                    "WHERE id=%s AND (improvement_note IS NULL OR improvement_note='')",
                                    (improvement_note, lid),
                                )
                if best_email:
                    result["enriched"] += 1
                    log.info(
                        f"enrich: {domain} → {best_email['email']} "
                        f"({best_email['confidence']})"
                    )

                    # Enroll each lead in the auto outreach campaign
                    if campaign_id and steps:
                        for lid in lead_ids:
                            if _enroll_lead(lid, campaign_id, steps):
                                result["enrolled"] += 1
                else:
                    log.info(f"enrich: {domain} → no emails found")

            browser.close()

    except Exception as e:
        log.error(f"enrich job crashed: {e}", exc_info=True)
        result["error"] = str(e)[:300]

    state["jobs"]["enrich"] = result
    _write_state(state)
    log.info(f"enrich: done {result}")


def job_send(state: dict):
    """Process due scheduled sends."""
    log.info("send: processing queue")
    result = {"at": _now_str(), "sent": 0, "failed": 0, "skipped": 0,
              "deferred": 0, "error": None}

    smtp     = SMTPConfig.from_env()
    from_addr = os.getenv("SMTP_FROM") or smtp.username

    if not smtp.host:
        log.warning("send: SMTP_HOST not configured — skipping")
        result["error"] = "SMTP not configured (set SMTP_HOST in .env)"
        state["jobs"]["send"] = result
        _write_state(state)
        return

    try:
        summary = process_queue(smtp, from_addr, limit=30)
        result.update({
            "sent":     summary["sent"],
            "failed":   summary["failed"],
            "skipped":  summary["skipped"],
            "deferred": summary.get("deferred", 0),
        })
        if any(result[k] for k in ("sent", "failed", "skipped", "deferred")):
            log.info(
                f"send: sent={result['sent']} "
                f"failed={result['failed']} "
                f"skipped={result['skipped']} "
                f"deferred={result['deferred']}"
            )
    except Exception as e:
        log.error(f"send job crashed: {e}", exc_info=True)
        result["error"] = str(e)[:300]

    state["jobs"]["send"] = result
    _write_state(state)


def job_inbox(state: dict):
    """Poll IMAP for replies. check_inbox() handles status + follow-up cancellation."""
    log.info("inbox: polling")
    result = {"at": _now_str(), "replies": 0, "error": None}

    try:
        from inbox import IMAPConfig, check_inbox
        cfg = IMAPConfig.from_env()
        if not cfg.ready:
            log.info("inbox: IMAP not configured — skipping")
            result["error"] = "IMAP not configured (set IMAP_HOST in .env)"
            state["jobs"]["inbox"] = result
            _write_state(state)
            return

        replies = check_inbox(cfg, limit=50, mark_seen=True)
        result["replies"] = len(replies) if replies else 0
        if replies:
            log.info(f"inbox: {len(replies)} reply(ies) matched and stored")
    except Exception as e:
        log.error(f"inbox job crashed: {e}", exc_info=True)
        result["error"] = str(e)[:300]

    state["jobs"]["inbox"] = result
    _write_state(state)


# ── Scrape rotation job ──────────────────────────────────────
def job_scrape_rotation(state: dict):
    """One tick of saturation-aware city rotation.

    Picks the most-deserving query (never-scraped first, then most-saturated-
    but-overdue), runs Google Maps scraping for it (no email extraction —
    the 30s enrich job picks those up), then updates next_run_at via
    adaptive backoff based on how many leads were actually new.
    """
    log.info("rotation: tick")
    result = {"at": _now_str(), "query": None, "leads_added": 0,
              "dedup_rate": None, "next_in_hrs": None, "error": None}

    try:
        from scrape_planner import (
            pick_next_query, query_target_leads,
            record_run_result, count_leads_for,
        )

        q = pick_next_query()
        if not q:
            log.info("rotation: no due queries")
            state["jobs"]["rotation"] = result
            _write_state(state)
            return

        result["query"] = f"{q['niche']} · {q['city']} · {q['country']}"
        target = query_target_leads(q["niche"], q["country"])
        log.info(f"rotation: {result['query']} (target={target})")

        before = count_leads_for(q["city"], q["country"])

        # Local import — avoids loading Playwright at worker startup.
        from pipeline import run_pipeline
        run_pipeline(
            searches=[{
                "niche":   q["niche"],
                "city":    q["city"],
                "country": q["country"],
            }],
            max_leads=target,
            headless=True,
            enrich_emails=False,    # the 30s enrich job handles emails
            on_event=lambda kind, msg, **_: None,
        )

        after = count_leads_for(q["city"], q["country"])
        leads_added = max(0, after - before)
        info = record_run_result(q["id"], leads_added, target)

        result["leads_added"] = leads_added
        result["dedup_rate"]  = info["dedup_rate"]
        result["next_in_hrs"] = info["next_in_hrs"]
        log.info(
            f"rotation: {result['query']} → +{leads_added} leads "
            f"(dedup_rate={info['dedup_rate']}, next in {info['next_in_hrs']}h)"
        )

    except Exception as e:
        log.error(f"rotation job crashed: {e}", exc_info=True)
        result["error"] = str(e)[:300]
        # Best-effort: record the error against the query if we got that far
        try:
            if "q" in locals() and q:
                from scrape_planner import record_run_result
                record_run_result(q["id"], 0, 1, error=str(e))
        except Exception:
            pass

    state["jobs"]["rotation"] = result
    _write_state(state)


# ── Entry point ──────────────────────────────────────────────
def main():
    log.info("Worker starting…")
    db.init_db()

    state: dict = {
        "pid":              os.getpid(),
        "started_at":       _now_str(),
        "auto_campaign_id": None,
        "jobs": {
            "enrich":   {},
            "send":     {},
            "inbox":    {},
            "rotation": {},
        },
    }
    _write_state(state)

    log.info("Bootstrapping auto campaign…")
    campaign_id, steps = _ensure_auto_campaign()
    state["auto_campaign_id"] = campaign_id
    _write_state(state)
    log.info(f"Auto campaign id={campaign_id}, {len(steps)} step(s)")

    now_utc = datetime.now(timezone.utc)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        lambda: job_enrich(state, campaign_id, steps),
        "interval", minutes=5, id="enrich", max_instances=1,
        next_run_time=now_utc,                           # run immediately on start
    )
    scheduler.add_job(
        lambda: job_send(state),
        "interval", minutes=2, id="send", max_instances=1,
        next_run_time=now_utc + timedelta(seconds=30),   # give enrich a head-start
    )
    scheduler.add_job(
        lambda: job_inbox(state),
        "interval", minutes=15, id="inbox", max_instances=1,
        next_run_time=now_utc + timedelta(minutes=1),
    )
    scheduler.add_job(
        lambda: job_scrape_rotation(state),
        "interval", hours=1, id="rotation", max_instances=1,
        next_run_time=now_utc + timedelta(seconds=60),  # first tick after warmup
    )

    log.info(
        "Worker running. Press Ctrl+C to stop.\n"
        "  enrich    → every 30 s\n"
        "  send      → every 60 s\n"
        "  inbox     → every 10 min\n"
        "  rotation  → every 1 h\n"
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Worker stopped.")
    finally:
        state["stopped_at"] = _now_str()
        _write_state(state)


if __name__ == "__main__":
    main()
