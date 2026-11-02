"""
Analytics endpoints — overview stats, trend, top domains.
"""

from datetime import datetime, timedelta, timezone

import db
from api.deps import get_org_id
from fastapi import APIRouter, Depends

router = APIRouter()


@router.get("/overview")
def overview(org_id: str = Depends(get_org_id)):
    """Headline KPIs scoped to the active organization."""
    with db.get_conn() as c:
        def n(q, *args):
            row = c.execute(q, args or ()).fetchone()
            return next(iter(row.values())) if row else 0

        # Plain filter for single-table queries on `leads`
        leads_filter = "org_id = %s"
        # Qualified filter for joins where multiple tables now have user_id
        l_filter = "e.org_id = %s"

        return {
            "leads_total":      n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter}", org_id),
            "emails_total":     n(f"SELECT COUNT(*) FROM emails e WHERE {l_filter}", org_id),
            "domains_total":    n(f"SELECT COUNT(DISTINCT domain) FROM leads WHERE {leads_filter} AND domain IS NOT NULL", org_id),
            "emails_high_conf": n(f"SELECT COUNT(*) FROM emails e WHERE {l_filter} AND e.confidence='high'", org_id),
            # Bucket counts
            "leads_callable":  n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter} AND phone IS NOT NULL AND phone!=''", org_id),
            "leads_emailable": n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter} AND email IS NOT NULL AND email!=''", org_id),
            "leads_pending":   n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter} AND (email IS NULL OR email='') AND domain IS NOT NULL AND domain!=''", org_id),
            "leads_no_contact":n(f"SELECT COUNT(*) FROM leads WHERE {leads_filter} AND (phone IS NULL OR phone='') AND (email IS NULL OR email='') AND (domain IS NULL OR domain='')", org_id),
        }


@router.get("/trend")
def trend(days: int = 14, org_id: str = Depends(get_org_id)):
    """Email extraction counts per day over the last N days."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).date().isoformat()
    with db.get_conn() as c:
        rows = c.execute(
            """
            SELECT SUBSTR(e.extracted_at, 1, 10) AS day, COUNT(*) AS n
            FROM emails e
            WHERE e.extracted_at >= %s
              AND e.org_id = %s
            GROUP BY day ORDER BY day
            """,
            (cutoff, org_id),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/top-domains")
def top_domains(limit: int = 25, org_id: str = Depends(get_org_id)):
    with db.get_conn() as c:
        rows = c.execute(
            """
            SELECT e.domain,
                   COUNT(*) AS email_count,
                   SUM(CASE WHEN e.confidence='high' THEN 1 ELSE 0 END) AS high_conf
            FROM emails e
            WHERE e.org_id = %s
            GROUP BY e.domain
            ORDER BY email_count DESC
            LIMIT %s
            """,
            (org_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/lead-statuses")
def lead_statuses(org_id: str = Depends(get_org_id)):
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT status, COUNT(*) AS n FROM leads "
            "WHERE org_id=%s "
            "GROUP BY status ORDER BY n DESC",
            (org_id,),
        ).fetchall()
    return [dict(r) for r in rows]
