"""
Analytics endpoints — overview stats, trend, top domains.
"""

from datetime import datetime, timedelta, timezone

import db
from api.deps import get_user_id
from fastapi import APIRouter, Depends

router = APIRouter()


@router.get("/overview")
def overview(user_id: str = Depends(get_user_id)):
    """Headline KPIs scoped to the authenticated user."""
    with db.get_conn() as c:
        def n(q, *args):
            row = c.execute(q, args or ()).fetchone()
            return next(iter(row.values())) if row else 0

        # Plain filter for single-table queries on `leads`
        leads_filter = "(user_id = %s OR user_id IS NULL)"
        # Qualified filter for joins where multiple tables now have user_id
        l_filter = "(l.user_id = %s OR l.user_id IS NULL)"

        return {
            "leads_total":      n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter}", user_id),
            "emails_total":     n(f"SELECT COUNT(*) FROM emails e JOIN domains d ON d.id=e.domain_id JOIN leads l ON l.domain=d.domain WHERE {l_filter}", user_id),
            "domains_total":    n(f"SELECT COUNT(DISTINCT domain) FROM leads WHERE {leads_filter} AND domain IS NOT NULL", user_id),
            "emails_high_conf": n(f"SELECT COUNT(*) FROM emails e JOIN domains d ON d.id=e.domain_id JOIN leads l ON l.domain=d.domain WHERE {l_filter} AND e.confidence='high'", user_id),
            # Bucket counts
            "leads_callable":  n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter} AND phone IS NOT NULL AND phone!=''", user_id),
            "leads_emailable": n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter} AND email IS NOT NULL AND email!=''", user_id),
            "leads_pending":   n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter} AND (email IS NULL OR email='') AND domain IS NOT NULL AND domain!=''", user_id),
            "leads_no_contact":n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter} AND (phone IS NULL OR phone='') AND (email IS NULL OR email='') AND (domain IS NULL OR domain='')", user_id),
        }


@router.get("/trend")
def trend(days: int = 14, user_id: str = Depends(get_user_id)):
    """Email extraction counts per day over the last N days."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).date().isoformat()
    with db.get_conn() as c:
        rows = c.execute(
            """
            SELECT SUBSTR(e.extracted_at, 1, 10) AS day, COUNT(*) AS n
            FROM emails e
            JOIN domains d ON d.id = e.domain_id
            JOIN leads   l ON l.domain = d.domain
            WHERE e.extracted_at >= %s
              AND (l.user_id = %s OR l.user_id IS NULL)
            GROUP BY day ORDER BY day
            """,
            (cutoff, user_id),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/top-domains")
def top_domains(limit: int = 25, user_id: str = Depends(get_user_id)):
    with db.get_conn() as c:
        rows = c.execute(
            """
            SELECT e.domain,
                   COUNT(*) AS email_count,
                   SUM(CASE WHEN e.confidence='high' THEN 1 ELSE 0 END) AS high_conf
            FROM emails e
            JOIN domains d ON d.id = e.domain_id
            JOIN leads   l ON l.domain = e.domain
            WHERE (l.user_id = %s OR l.user_id IS NULL)
            GROUP BY e.domain
            ORDER BY email_count DESC
            LIMIT %s
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/lead-statuses")
def lead_statuses(user_id: str = Depends(get_user_id)):
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM leads "
            "WHERE (user_id=%s OR user_id IS NULL) "
            "GROUP BY status ORDER BY n DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]
