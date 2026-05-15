"""
Leads endpoints — list, detail, status update.

All queries are scoped to the authenticated user's user_id.
"""

from typing import Optional

import db
from api.deps import get_user_id
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter()


class LeadStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None


@router.get("")
def list_leads(
    bucket: str = "all",      # all | call | email | pending | none
    status: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    user_id: str = Depends(get_user_id),
):
    """
    Return leads for the authenticated user, segmented by contact readiness.
    Also returns bucket_counts so the frontend can render tab badges.
    """
    base_where = "WHERE (user_id = %s OR user_id IS NULL)"
    args: list = [user_id]

    bucket_sql = {
        "call":    "AND phone IS NOT NULL AND phone != ''",
        "email":   "AND email IS NOT NULL AND email != ''",
        "pending": "AND (email IS NULL OR email='') AND domain IS NOT NULL AND domain != ''",
        "none":    "AND (phone IS NULL OR phone='') AND (email IS NULL OR email='') AND (domain IS NULL OR domain='')",
    }

    filter_clause = bucket_sql.get(bucket, "")

    status_clause = ""
    if status:
        status_clause = "AND status = %s"
        args.append(status)

    search_clause = ""
    if q:
        search_clause = (
            "AND (business_name ILIKE %s OR city ILIKE %s "
            "OR domain ILIKE %s OR email ILIKE %s OR phone ILIKE %s)"
        )
        like = f"%{q}%"
        args.extend([like, like, like, like, like])

    with db.get_conn() as c:
        # Bucket counts (no search/status filter — always show full counts)
        def _count(extra_clause):
            row = c.execute(
                f"SELECT COUNT(*) AS n FROM leads {base_where} {extra_clause}",
                [user_id],
            ).fetchone()
            return row["n"] if row else 0

        bucket_counts = {
            "all":     _count(""),
            "call":    _count(bucket_sql["call"]),
            "email":   _count(bucket_sql["email"]),
            "pending": _count(bucket_sql["pending"]),
            "none":    _count(bucket_sql["none"]),
        }

        # Main query
        rows = c.execute(
            f"""
            SELECT id, source, business_name, domain, email, phone, website,
                   address, city, country, rating, reviews,
                   maps_url, improvement_note, last_review_days,
                   status, created_at
            FROM leads
            {base_where} {filter_clause} {status_clause} {search_clause}
            ORDER BY id DESC
            LIMIT %s OFFSET %s
            """,
            args + [limit, offset],
        ).fetchall()

    return {
        "items": [dict(r) for r in rows],
        "total": bucket_counts.get(bucket, 0),
        "bucket_counts": bucket_counts,
    }


@router.get("/{lead_id}")
def get_lead(lead_id: int, user_id: str = Depends(get_user_id)):
    with db.get_conn() as c:
        row = c.execute(
            "SELECT * FROM leads WHERE id=%s AND (user_id=%s OR user_id IS NULL)",
            (lead_id, user_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    return dict(row)


@router.patch("/{lead_id}")
def update_lead(lead_id: int, body: LeadStatusUpdate, user_id: str = Depends(get_user_id)):
    with db.get_conn() as c:
        existing = c.execute(
            "SELECT id FROM leads WHERE id=%s AND (user_id=%s OR user_id IS NULL)",
            (lead_id, user_id),
        ).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Lead not found")
        c.execute(
            "UPDATE leads SET status=%s WHERE id=%s",
            (body.status, lead_id),
        )
        if body.notes:
            c.execute(
                "UPDATE leads SET notes=COALESCE(notes,'') || %s WHERE id=%s",
                (f"\n[{db.now()}] {body.notes}", lead_id),
            )
    return {"ok": True}
