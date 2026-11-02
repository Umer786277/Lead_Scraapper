"""
Automation worker — background process driving the email pipeline.

Jobs (all run independently via APScheduler):
  enrich  (every 5 min): new leads with domains → extract contact details
  send    (every 60 s): process the outreach send queue via SMTP
  inbox   (every 2 min): IMAP poll for replies → cancel follow-ups, react to intents

Leads are no longer automatically enrolled in the legacy four-step campaign.
Users start campaigns explicitly from the Leads page.

Run as a separate process, or use the API's integrated worker runner:
    pip install apscheduler
    python worker.py

Status is written to worker_state.json and exposed by the API.
"""

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Windows consoles default to the cp1252 codepage, which can't encode the
# emoji used in scraper log/print output and crashes the run mid-way when
# this file is executed standalone (python worker.py).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# Shared local lock for the enrich job. Scrape pipelines use per-org distributed
# locks from locks.py so different organizations can scrape independently.
from locks import browser_lock as _browser_lock, browser_lock_for

# Throttle DB heartbeat writes — lightweight jobs (send/calls) run every 1-2 min
# but we only need to persist heartbeat to DB once every 2 min.
_last_db_heartbeat: float = 0

# Shared Chromium launch args — tuned to minimise RAM on Render's 512 MB free tier.
_CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-gpu",
    "--no-first-run",
    "--disable-extensions",
    "--disable-default-apps",
    "--disable-background-networking",
    "--disable-sync",
    "--mute-audio",
    "--disable-features=TranslateUI",
]

# Load .env BEFORE any module that calls os.getenv at import time —
# otherwise SMTP / IMAP / Google AI Studio env vars come back empty on first job tick.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Windows asyncio fix — Playwright requires subprocess support, so set this
# before importing Playwright-dependent modules.
if sys.platform == "win32":
    import asyncio
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from apscheduler.schedulers.blocking import BlockingScheduler

import db
from coordination import run_scheduled_job
from outreach import SMTPConfig, process_queue

# ── Config ───────────────────────────────────────────────────
ENRICH_BATCH        = 10    # max domains per enrich run
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


def _write_state(state: dict, force_db: bool = False):
    global _last_db_heartbeat
    state_file = Path(state.get("state_file") or STATE_FILE)
    try:
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"Could not write state file: {e}")
    # Throttle DB heartbeat to once every 2 minutes — lightweight jobs (send, calls)
    # run every 1-2 min and calling this on every tick wastes Supabase connections.
    now_ts = time.time()
    if force_db or (now_ts - _last_db_heartbeat) >= 120:
        try:
            db.worker_heartbeat_write(
                pid=state.get("pid", os.getpid()),
                started_at=state.get("started_at", _now_str()),
                jobs=state.get("jobs", {}),
            )
            _last_db_heartbeat = now_ts
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


# ── AI improvement note + client-facing lead line ─────────────
def _generate_improvement_note(domain: str, snippet: str) -> dict | None:
    """Generate the AI note + lead_line for a domain's snippet.

    One underlying LLM call returns both: the internal prep note
    (leads.improvement_note) and a client-facing opening line
    (leads.lead_line) that's safe to drop into outreach copy.

    Delegates to llm.generate_improvement_note (single shared implementation).
    """
    if not snippet:
        return None
    try:
        from llm import generate_improvement_note as _gen
        return _gen(domain, snippet)
    except Exception as e:
        log.debug(f"AI note failed for {domain}: {e}")
        return None


# ── Jobs ─────────────────────────────────────────────────────
def job_enrich(state: dict):
    """Find leads with domains but no email and extract available contact details."""
    log.info("enrich: starting")
    result = {"at": _now_str(), "found": 0, "enriched": 0, "enrolled": 0, "error": None}

    try:
        with db.get_conn() as c:
            rows = c.execute(
                """
                SELECT l.org_id, l.domain,
                       ARRAY_AGG(l.id ORDER BY l.id) AS lead_ids
                FROM leads l
                WHERE l.status = 'new'
                  AND l.domain IS NOT NULL AND l.domain != ''
                  AND (l.email IS NULL OR l.email = '')
                  AND NOT EXISTS (
                      SELECT 1 FROM domains d
                      WHERE d.domain = l.domain AND d.org_id = l.org_id
                  )
                GROUP BY l.org_id, l.domain
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

        if not _browser_lock.acquire(blocking=False):
            log.info("enrich: browser lock held by another job — skipping this tick")
            state["jobs"]["enrich"] = result
            _write_state(state)
            return

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=_CHROMIUM_ARGS)
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1024, "height": 600},
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
                    org      = row["org_id"]
                    domain   = row["domain"]
                    lead_ids = row["lead_ids"]
                    log.info(f"enrich: [{org}] {domain} (leads: {lead_ids})")

                    try:
                        extraction = extract_from_domain(domain, context)
                    except Exception as e:
                        log.warning(f"enrich: {domain} failed — {e}")
                        # Still record the domain so we don't retry every 30 s
                        try:
                            db.insert_domain(
                                run_id=None, domain=domain, status="failed",
                                has_mx=None, pages_visited=0, emails_found=0,
                                error=str(e)[:200], org_id=org,
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
                        org_id=org,
                    )

                    best_email = None
                    snippet = extraction.get("snippet")
                    has_mx  = extraction.get("has_mx")
                    ai = _generate_improvement_note(domain, snippet) if snippet else None

                    # Batch: all email inserts + lead updates in one connection per domain
                    with db.get_conn() as c:
                        for e in extraction.get("emails", []):
                            try:
                                c.execute(
                                    "INSERT INTO emails "
                                    "(org_id, domain_id, domain, email, source_url, source_type, "
                                    " confidence, is_role, category, verification_status, extracted_at) "
                                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                                    "ON CONFLICT (org_id, domain, email) DO NOTHING",
                                    (
                                        db.org_id_or_default(org),
                                        domain_id, domain, e["email"],
                                        e.get("source_url"), e.get("source_type"),
                                        e.get("confidence"), int(bool(e.get("is_role"))),
                                        e.get("category"),
                                        "valid_mx" if has_mx else "no_mx",
                                        db.now(),
                                    ),
                                )
                            except Exception:
                                pass
                            if best_email is None or _email_score(e) > _email_score(best_email):
                                best_email = e

                        if best_email or snippet:
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
                                if ai:
                                    c.execute(
                                        "UPDATE leads SET improvement_note=%s, lead_line=%s "
                                        "WHERE id=%s AND (improvement_note IS NULL OR improvement_note='')",
                                        (ai.get("note") or None, ai.get("lead_line") or None, lid),
                                    )
                    if best_email:
                        result["enriched"] += 1
                        log.info(
                            f"enrich: {domain} → {best_email['email']} "
                            f"({best_email['confidence']})"
                        )
                    else:
                        log.info(f"enrich: {domain} → no emails found")

                browser.close()
        finally:
            _browser_lock.release()

    except Exception as e:
        log.error(f"enrich job crashed: {e}", exc_info=True)
        result["error"] = str(e)[:300]

    # Phase 2: backfill AI notes/lead lines for leads that already have a
    # snippet but never got them (the note, the client-facing lead_line, or
    # either — no browser needed here). One LLM call produces both.
    if os.getenv("GOOGLE_AI_STUDIO_API_KEY"):
        try:
            with db.get_conn() as c:
                note_rows = c.execute(
                    """
                    SELECT id, domain, homepage_snippet
                    FROM leads
                    WHERE lead_line IS NULL
                      AND homepage_snippet IS NOT NULL AND homepage_snippet != ''
                    LIMIT 20
                    """,
                ).fetchall()
            note_count = 0
            for nr in note_rows:
                ai = _generate_improvement_note(nr["domain"] or "", nr["homepage_snippet"])
                if ai:
                    with db.get_conn() as c:
                        c.execute(
                            "UPDATE leads SET improvement_note=%s, lead_line=%s WHERE id=%s",
                            (ai.get("note") or None, ai.get("lead_line") or None, nr["id"]),
                        )
                    note_count += 1
            if note_count:
                log.info(f"enrich: backfilled {note_count} AI notes from existing snippets")
        except Exception as e:
            log.warning(f"enrich: AI note backfill failed — {e}")

    state["jobs"]["enrich"] = result
    _write_state(state, force_db=True)   # enrich is heavy — always persist its result
    log.info(f"enrich: done {result}")


def _on_send_item(item, outcome, detail):
    """Outbox/campaign callback - log per-lead outcomes for the live viewer."""
    lead_id = item.get("lead_id")
    step = item.get("step_number")
    who = (f"lead #{lead_id} "
           f"({item.get('business_name') or item.get('email') or item.get('to_email') or '?'})")
    suffix = f" step {step}" if step is not None else ""
    script_suffix = f" script={item['script_name']}" if item.get("script_name") else ""
    if outcome == "sent":
        log.info(f"send: {who}{suffix}{script_suffix} -> sent")
    elif outcome == "failed":
        log.warning(f"send: {who}{suffix}{script_suffix} -> FAILED: {detail}")
    elif outcome == "unknown":
        log.warning(f"send: {who}{suffix}{script_suffix} -> UNKNOWN (not retried): {detail}")
    elif outcome == "retry":
        log.info(f"send: {who}{suffix}{script_suffix} -> retry scheduled: {detail}")
    elif outcome == "dead_letter":
        log.error(f"send: {who}{suffix}{script_suffix} -> dead-lettered: {detail}")
    elif outcome == "circuit_broken":
        log.error(f"send: {detail}")
    elif outcome == "no_sender":
        log.warning(f"send: {detail}")
    elif outcome == "skipped":
        log.debug(f"send: {who}{script_suffix} -> skipped ({detail})")
    elif outcome == "deferred":
        log.debug(f"send: {who} -> deferred ({detail})")
    elif outcome == "queued":
        log.info(f"send: {who}{suffix}{script_suffix} -> queued ({detail})")

def job_send(state: dict):
    """Process due scheduled sends.

    Doesn't touch Chromium (just SMTP + an LLM API call for the opener), so —
    like job_calls below — it doesn't need to yield to _browser_lock. It used
    to, which meant a tick that happened to land while enrich/rotation held
    the lock was silently skipped (log.debug, invisible at INFO level) *and*
    never wrote state["jobs"]["send"], so a missed tick looked identical to
    "job never ran" for a full 5-minute interval — exactly the bug already
    fixed once for job_calls (see its docstring).
    """
    log.info("send: processing queue")
    result = {"at": _now_str(), "queued": 0, "sent": 0, "failed": 0,
              "skipped": 0, "deferred": 0, "retry": 0, "unknown": 0,
              "dead_letter": 0,
              "error": None}

    smtp     = SMTPConfig.from_env()
    from_addr = os.getenv("SMTP_FROM") or smtp.username

    if not smtp.host and not db.list_email_accounts():
        log.warning("send: no SMTP account configured — skipping")
        result["error"] = ("No sender account configured "
                           "(add one in Outreach → Accounts or set SMTP_HOST)")
        state["jobs"]["send"] = result
        _write_state(state)
        return

    try:
        orgs = db.list_active_orgs() or [db.DEFAULT_ORG_ID]
        for org in orgs:
            if not smtp.host and not db.list_email_accounts(org_id=org):
                continue
            summary = process_queue(smtp, from_addr, limit=30, on_item=_on_send_item,
                                    org_id=org)
            for k in ("queued", "sent", "failed", "skipped", "retry", "unknown",
                      "dead_letter"):
                result[k] += summary.get(k, 0)
            result["deferred"] += summary.get("deferred", 0)
            if summary.get("paused"):
                result["error"] = summary.get("error", "Outreach paused")
                log.info(f"send [{org}]: {result['error']}")
            elif summary.get("limit_reached"):
                result["error"] = summary["limit_reason"]
                log.info(f"send [{org}]: {result['error']}")
            if summary.get("circuit_broken"):
                result["error"] = summary["circuit_reason"]
                log.error(f"send [{org}]: circuit breaker tripped — {summary['circuit_reason']}")
                break
        if any(result[k] for k in
               ("queued", "sent", "failed", "skipped", "deferred", "retry",
                "unknown", "dead_letter")):
            log.info(
                f"send: queued={result['queued']} "
                f"sent={result['sent']} "
                f"failed={result['failed']} "
                f"skipped={result['skipped']} "
                f"retry={result['retry']} "
                f"unknown={result['unknown']} "
                f"dead_letter={result['dead_letter']} "
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
    result = {"at": _now_str(), "replies": 0, "accounts": 0, "error": None,
              "duration_s": 0.0}
    _t0 = time.perf_counter()

    try:
        from inbox import IMAPConfig, check_inbox

        targets = []
        for acct in db.list_imap_accounts():
            cfg = IMAPConfig.from_account(acct)
            if cfg.ready:
                targets.append((acct.get("org_id"), cfg, acct.get("id")))

        if not targets:
            cfg = IMAPConfig.from_env()
            if cfg.ready:
                targets.append((None, cfg, None))

        if not targets:
            log.info("inbox: IMAP not configured — skipping")
            result["error"] = ("IMAP not configured "
                               "(add an email account or set IMAP_HOST in .env)")
            state["jobs"]["inbox"] = result
            _write_state(state)
            return

        errors = []
        for org, cfg, account_id in targets:
            try:
                replies = check_inbox(
                    cfg, limit=50, mark_seen=True,
                    booking_link=os.getenv("BOOKING_LINK"),
                    org_id=org,
                    account_id=account_id,
                )
                processing_errors = [
                    reply for reply in replies if reply.get("error")
                ]
                matched_count = sum(
                    1 for reply in replies
                    if reply.get("lead_id") is not None and not reply.get("error")
                )
                result["replies"] += matched_count
                result["accounts"] += 1
                if processing_errors:
                    errors.append(
                        f"{org or 'env'}: {len(processing_errors)} inbound "
                        "message(s) need retry or dead-letter review"
                    )
                if replies:
                    log.info(f"inbox [{org or 'env'}]: "
                             f"{matched_count} reply(ies) matched and stored")
            except Exception as e:
                log.error(f"inbox [{org or 'env'}] crashed: {e}", exc_info=True)
                errors.append(f"{org or 'env'}: {e}")
        if errors:
            result["error"] = "; ".join(errors)[:300]
    except Exception as e:
        log.error(f"inbox job crashed: {e}", exc_info=True)
        result["error"] = str(e)[:300]

    result["duration_s"] = round(time.perf_counter() - _t0, 1)
    state["jobs"]["inbox"] = result
    _write_state(state)


# ── Pending booking replies job ───────────────────────────────
def job_booking_replies(state: dict):
    """Queue deferred booking invitations and deliver due outbox rows."""
    log.info("booking_replies: checking pending")
    result = {"at": _now_str(), "queued": 0, "sent": 0, "failed": 0,
              "retry": 0, "unknown": 0, "dead_letter": 0,
              "error": None, "duration_s": 0.0}
    _t0 = time.perf_counter()

    try:
        booking_link = os.getenv("BOOKING_LINK")
        if not booking_link:
            log.info("booking_replies: BOOKING_LINK not configured - skipping")
            result["error"] = "BOOKING_LINK not configured"
            state["jobs"]["booking_replies"] = result
            _write_state(state)
            return

        from inbox import drain_pending_replies
        from outreach import process_outbound_queue

        smtp = SMTPConfig.from_env()
        from_addr = os.getenv("SMTP_FROM") or smtp.username

        for org in (db.list_active_orgs() or [db.DEFAULT_ORG_ID]):
            result["queued"] += drain_pending_replies(
                None, None, booking_link, limit=10, org_id=org,
            )
            if not smtp.host and not db.list_email_accounts(org_id=org):
                continue
            summary = process_outbound_queue(
                smtp=smtp if smtp.host else None,
                from_addr=from_addr if smtp.host else None,
                limit=10,
                on_item=_on_send_item,
                org_id=org,
            )
            for k in ("sent", "failed", "retry", "unknown", "dead_letter"):
                result[k] += summary.get(k, 0)
            if summary.get("paused") or summary.get("limit_reached"):
                result["error"] = summary.get("error") or summary.get("limit_reason")
        if result["queued"] or result["sent"]:
            log.info(
                f"booking_replies: queued={result['queued']} sent={result['sent']}"
            )
    except Exception as e:
        log.error(f"booking_replies job crashed: {e}", exc_info=True)
        result["error"] = str(e)[:300]

    result["duration_s"] = round(time.perf_counter() - _t0, 1)
    state["jobs"]["booking_replies"] = result
    _write_state(state, force_db=True)

def job_scrape_rotation(state: dict, org_id=None, persist_state=True):
    """Run one due saturation-aware city scrape for an organization."""
    from scrape_planner import (
        count_leads_for,
        pick_next_query,
        query_target_leads,
        record_run_result,
    )

    log.info("rotation: tick%s", f" [{org_id}]" if org_id else "")
    result = {
        "at": _now_str(),
        "org_id": str(org_id) if org_id is not None else None,
        "query": None,
        "leads_added": 0,
        "dedup_rate": None,
        "next_in_hrs": None,
        "error": None,
    }
    query = None

    try:
        query = pick_next_query(org_id=org_id)
        if not query:
            log.info("rotation: no due queries%s", f" [{org_id}]" if org_id else "")
        else:
            query_org_id = org_id or query["org_id"]
            result["org_id"] = str(query_org_id)
            result["query"] = (
                f"{query['niche']} · {query['city']} · {query['country']}"
            )
            target = query_target_leads(
                query_org_id, query["niche"], query["country"],
            )
            log.info(
                "rotation: %s [%s] (target=%s)",
                result["query"], query_org_id, target,
            )

            before = count_leads_for(
                query["city"], query["country"], org_id=query_org_id,
            )

            from pipeline import run_pipeline
            run_pipeline(
                searches=[{
                    "niche": query["niche"],
                    "city": query["city"],
                    "country": query["country"],
                }],
                max_leads=target,
                headless=True,
                enrich_emails=False,
                on_event=lambda kind, message, **extra: None,
                org_id=query_org_id,
            )

            after = count_leads_for(
                query["city"], query["country"], org_id=query_org_id,
            )
            leads_added = max(0, after - before)
            info = record_run_result(query["id"], leads_added, target)
            result["leads_added"] = leads_added
            result["dedup_rate"] = info["dedup_rate"]
            result["next_in_hrs"] = info["next_in_hrs"]
            log.info(
                "rotation: %s [%s] -> +%s leads "
                "(dedup_rate=%s, next in %sh)",
                result["query"], query_org_id, leads_added,
                info["dedup_rate"], info["next_in_hrs"],
            )
    except Exception as exc:
        log.error("rotation job crashed [%s]: %s", org_id, exc, exc_info=True)
        result["error"] = str(exc)[:300]
        if query:
            try:
                record_run_result(query["id"], 0, 1, error=str(exc))
            except Exception:
                log.exception(
                    "rotation: failed to record scrape error for query %s",
                    query["id"],
                )
    finally:
        state["jobs"]["rotation"] = result
        if persist_state:
            _write_state(state)

    return result


def job_scrape_rotation_all(state: dict):
    """Run one due scrape per organization with independent distributed locks."""
    from concurrent.futures import ThreadPoolExecutor

    orgs = db.list_active_orgs() or [db.DEFAULT_ORG_ID]
    configured_workers = int(os.getenv("SCRAPE_WORKER_CONCURRENCY", "2"))
    if configured_workers < 1:
        raise ValueError("SCRAPE_WORKER_CONCURRENCY must be at least 1.")

    def run_for_org(org_id):
        return run_scheduled_job(
            f"rotation:{org_id}",
            lambda: job_scrape_rotation(state, org_id=org_id, persist_state=False),
        )

    with ThreadPoolExecutor(max_workers=min(configured_workers, len(orgs))) as executor:
        list(executor.map(run_for_org, orgs))
    _write_state(state, force_db=True)


def job_calls(state: dict):
    """Dispatch queued outbound calls via Vapi.ai.

    Unlike enrich/send/rotation, this never touches Chromium — it's a plain
    HTTP POST to Vapi — so it doesn't need to yield to _browser_lock. It used
    to, which meant it silently starved (via a log.debug skip line, invisible
    at the app's INFO log level) whenever scraping was active — worse once
    rotation started ticking every ~8.5 min instead of hourly.
    """
    log.info("calls: checking queue")
    result = {"at": _now_str(), "dispatched": 0, "failed": 0, "error": None, "duration_s": 0.0}
    _t0 = time.perf_counter()

    vapi_key = os.getenv("VAPI_API_KEY", "")
    if not vapi_key:
        result["error"] = "VAPI_API_KEY not set — skipping"
        state["jobs"]["calls"] = result
        _write_state(state)
        return

    try:
        from voice_caller import dispatch_call
        queued = db.get_queued_calls(limit=5)

        for call_row in queued:
            try:
                vapi_call = dispatch_call(call_row)
                db.update_call(
                    call_row["id"],
                    status="initiated",
                    vapi_call_id=vapi_call.get("id", ""),
                    initiated_at=db.now(),
                )
                result["dispatched"] += 1
                log.info(f"calls: dispatched {vapi_call.get('id')} for lead {call_row['lead_id']}")
            except Exception as e:
                db.update_call(call_row["id"], status="failed", notes=str(e)[:200])
                result["failed"] += 1
                log.warning(f"calls: failed lead {call_row['lead_id']}: {e}")

    except Exception as e:
        log.error(f"calls job crashed: {e}", exc_info=True)
        result["error"] = str(e)[:300]

    state["jobs"]["calls"] = result
    _write_state(state)
    result["duration_s"] = round(time.perf_counter() - _t0, 1)
    log.info(f"calls: done {result}")
    log.info(f"calls: done {result}")


# ── Entry point ──────────────────────────────────────────────
def main():
    log.info("Worker starting…")
    db.init_db()

    state: dict = {
        "pid":              os.getpid(),
        "started_at":       _now_str(),
        "state_file":       str(STATE_FILE),
        "process":          "main",
        "auto_campaign_id": None,
        "jobs": {
            "enrich":   {},
            "send":     {},
            "rotation": {},
            "calls":    {},
        },
    }
    _write_state(state)

    log.info("Automatic lead enrollment is disabled; campaigns start from the Leads page.")

    now_utc = datetime.now(timezone.utc)

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        lambda: run_scheduled_job("enrich", lambda: job_enrich(state)),
        "interval", minutes=5, id="enrich", max_instances=1,
        next_run_time=now_utc,                           # run immediately on start
        jitter=30,                                       # de-collide 5-min bursts
    )
    scheduler.add_job(
        lambda: run_scheduled_job("send", lambda: job_send(state)),
        "interval", minutes=2, id="send", max_instances=1,
        next_run_time=now_utc + timedelta(seconds=30),   # give enrich a head-start
    )
    scheduler.add_job(
        lambda: run_scheduled_job(
            "rotation", lambda: job_scrape_rotation_all(state)
        ),
        "interval", hours=1, id="rotation", max_instances=1,
        next_run_time=now_utc + timedelta(seconds=60),  # first tick after warmup
    )
    scheduler.add_job(
        lambda: run_scheduled_job("calls", lambda: job_calls(state)),
        "interval", minutes=5, id="calls", max_instances=1,
        next_run_time=now_utc + timedelta(minutes=1),
        jitter=30,                                      # de-collide 5-min bursts
    )

    log.info(
        "Worker running. Press Ctrl+C to stop.\n"
        "  enrich          → every 5 min (browser)\n"
        "  send            → every 2 min\n"
        "  rotation        → every 1 h (browser)\n"
        "  calls           → every 5 min\n"
        "  (inbox + booking_replies now run in worker_replies.py)\n"
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
