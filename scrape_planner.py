"""
Saturation-aware city rotation for recurring scrapes.

Two concepts:

  - schedule (`scrape_schedules`): user-facing — "I want pet clinics in the UK
    on a recurring basis." One row per (user, niche, country).

  - query (`scrape_queries`): one unit of work per (niche, city, country).
    Shared across users — if two users register the same niche/country, they
    share the same query rows and the same backoff state.

The hourly worker calls `pick_next_query()`, runs it, then calls
`record_run_result()` with how many leads were actually new. The planner
adapts `next_run_at` based on dedup rate so saturated cities get scraped
less often, fresh ones more often.
"""

from datetime import datetime, timedelta, timezone

import db
from cities import cities_for

# Backoff bounds — keep saturated cities from re-scraping too often, but
# never wait longer than a week.
MIN_BACKOFF_HOURS = 1
MAX_BACKOFF_HOURS = 24 * 7
DEFAULT_BACKOFF_HOURS = 6   # fresh queries with no signal yet


# ── Schedule registration ─────────────────────────────────────
def register_schedule(user_id: str, niche: str, country: str,
                      target_leads: int = 20) -> dict:
    """Create a schedule and seed scrape_queries for every city in the country.

    Idempotent on the (niche, city, country) UNIQUE constraint — re-registering
    the same schedule will create a new schedule row but reuse existing query
    rows (and their backoff state) so progress isn't lost.
    """
    niche = (niche or "").strip()
    country = (country or "").strip()
    if not niche or not country:
        raise ValueError("niche and country are required")

    cities = cities_for(country)
    if not cities:
        raise ValueError(f"No cities available for country '{country}' "
                         f"(unknown to static map and LLM fetch failed).")

    with db.get_conn() as c:
        cur = c.execute(
            "INSERT INTO scrape_schedules (user_id, niche, country, target_leads) "
            "VALUES (%s, %s, %s, %s) RETURNING id",
            (user_id, niche, country, target_leads),
        )
        schedule_id = cur.fetchone()["id"]

        # Seed queries — one per (niche, city, country). Existing rows keep
        # their backoff state thanks to ON CONFLICT DO NOTHING.
        for city in cities:
            c.execute(
                "INSERT INTO scrape_queries (niche, city, country) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (niche, city, country) DO NOTHING",
                (niche, city, country),
            )

    return {"schedule_id": schedule_id, "queries_seeded": len(cities)}


def list_schedules(user_id: str) -> list[dict]:
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT id, niche, country, target_leads, status, created_at "
            "FROM scrape_schedules WHERE user_id = %s "
            "ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def cancel_schedule(user_id: str, schedule_id: int) -> bool:
    """Mark a schedule paused. Query rows stay (other users may share them)."""
    with db.get_conn() as c:
        cur = c.execute(
            "UPDATE scrape_schedules SET status = 'paused' "
            "WHERE id = %s AND user_id = %s",
            (schedule_id, user_id),
        )
        return cur.rowcount > 0


# ── Query selection ───────────────────────────────────────────
def pick_next_query() -> dict | None:
    """Pick the most-deserving due query atomically.

    SKIP LOCKED so multiple workers can run in parallel without picking the
    same row. Ordering: never-scraped first, then most-saturated-but-overdue.
    """
    with db.get_conn() as c:
        # Find a query that:
        #   - Has at least one active schedule covering its (niche, country)
        #   - Is due (next_run_at NULL or in the past)
        # Order by: never scraped first, then oldest scrape, then fewest leads.
        row = c.execute(
            """
            SELECT q.*
            FROM scrape_queries q
            WHERE EXISTS (
                SELECT 1 FROM scrape_schedules s
                WHERE s.niche   = q.niche
                  AND s.country = q.country
                  AND s.status  = 'active'
            )
              AND (q.next_run_at IS NULL OR q.next_run_at <= CURRENT_TIMESTAMP)
            ORDER BY q.last_scraped_at NULLS FIRST,
                     q.lead_count ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()
    return dict(row) if row else None


def query_target_leads(niche: str, country: str) -> int:
    """The largest target_leads of any active schedule covering this query."""
    with db.get_conn() as c:
        row = c.execute(
            "SELECT MAX(target_leads) AS n FROM scrape_schedules "
            "WHERE niche = %s AND country = %s AND status = 'active'",
            (niche, country),
        ).fetchone()
    return (row and row["n"]) or 20


# ── Result recording / adaptive backoff ───────────────────────
def _next_backoff_hours(prev_run_count: int, dedup_rate: float | None,
                        had_error: bool) -> int:
    """How many hours until this query should run again."""
    if had_error:
        # Exponential-ish backoff on errors, capped.
        return min(MAX_BACKOFF_HOURS, max(MIN_BACKOFF_HOURS, 2 ** prev_run_count))

    if dedup_rate is None:
        return DEFAULT_BACKOFF_HOURS

    # >70% duplicates → city is saturated, push out further
    # 30–70% → keep current cadence (default)
    # <30% duplicates → fresh territory, pull in
    if dedup_rate >= 0.7:
        return min(MAX_BACKOFF_HOURS, DEFAULT_BACKOFF_HOURS * 4)
    if dedup_rate <= 0.3:
        return MIN_BACKOFF_HOURS
    return DEFAULT_BACKOFF_HOURS


def record_run_result(query_id: int, leads_added: int, target: int,
                      error: str | None = None) -> dict:
    """Update query state after a scrape. Returns the new state dict."""
    dedup_rate = None
    if not error and target > 0:
        # leads_added can exceed target if Maps returned bonus rows — clamp at 1.
        new_fraction = min(1.0, leads_added / target)
        dedup_rate = max(0.0, 1.0 - new_fraction)

    with db.get_conn() as c:
        prev = c.execute(
            "SELECT runs_total FROM scrape_queries WHERE id = %s",
            (query_id,),
        ).fetchone()
        runs_total = (prev["runs_total"] if prev else 0) + 1

    next_hours = _next_backoff_hours(runs_total, dedup_rate, bool(error))
    next_run_at = datetime.now(timezone.utc) + timedelta(hours=next_hours)

    with db.get_conn() as c:
        c.execute(
            """
            UPDATE scrape_queries
            SET lead_count      = lead_count + %s,
                dedup_rate      = COALESCE(%s, dedup_rate),
                last_scraped_at = CURRENT_TIMESTAMP,
                next_run_at     = %s,
                last_error      = %s,
                runs_total      = runs_total + 1
            WHERE id = %s
            """,
            (
                max(0, leads_added),
                dedup_rate,
                next_run_at,
                error[:300] if error else None,
                query_id,
            ),
        )

    return {
        "query_id":    query_id,
        "leads_added": leads_added,
        "dedup_rate":  dedup_rate,
        "next_run_at": next_run_at.isoformat(),
        "next_in_hrs": next_hours,
    }


# ── Helper: count existing leads for a (city, country) — used by the
#    worker to compute leads_added from a delta.
def count_leads_for(city: str, country: str) -> int:
    with db.get_conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM leads WHERE city = %s AND country = %s",
            (city, country),
        ).fetchone()
    return (row and row["n"]) or 0
