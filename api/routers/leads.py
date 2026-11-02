"""
Leads endpoints — list, detail, status update.

All queries are scoped to the active organization.
"""

import csv
import io
import re
from typing import List, Optional
from urllib.parse import urlparse

import db
from api.deps import get_org_id
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

router = APIRouter()

MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 10_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CSV_FIELDS = {
    "business_name": {"business", "business_name", "company", "company_name", "organization", "name"},
    "domain": {"domain", "website_domain", "company_domain"},
    "email": {"email", "email_address", "contact_email", "extracted_email"},
    "phone": {"phone", "phone_number", "telephone", "tel"},
    "website": {"website", "website_url", "company_website", "url", "site"},
    "address": {"address", "street_address"},
    "city": {"city", "locality"},
    "country": {"country", "nation"},
    "rating": {"rating", "stars"},
    "reviews": {"reviews", "review_count", "reviews_count"},
    "maps_url": {"maps_url", "google_maps_url", "map_url", "place_url"},
    "improvement_note": {"improvement_note", "ai_note"},
    "lead_line": {"lead_line"},
    "last_review_days": {"last_review_days"},
    "contact_name": {"contact_name", "contact_person"},
    "homepage_snippet": {"homepage_snippet", "snippet", "description"},
    "notes": {"notes", "note"},
}
_CSV_HEADER_FIELDS = {
    re.sub(r"[^a-z0-9]+", "_", alias.casefold()).strip("_"): field
    for field, aliases in CSV_FIELDS.items()
    for alias in aliases
}


class LeadStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None


class LeadIdsBody(BaseModel):
    lead_ids: List[int]


def _csv_value(row, headers, field):
    for header in headers.get(field, []):
        value = row.get(header)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _optional_number(value, field, line_number, integer=False):
    if not value:
        return None
    try:
        return int(value) if integer else float(value)
    except ValueError as exc:
        raise ValueError(f"Row {line_number}: {field} must be a number.") from exc


def _normalize_domain(value):
    if not value:
        return None
    candidate = value.strip().lower()
    if "://" not in candidate:
        candidate = "https://" + candidate
    parsed = urlparse(candidate)
    return (parsed.hostname or "").removeprefix("www.") or None


def _parse_leads_csv(contents):
    try:
        text = contents.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400,
            detail="CSV must be encoded as UTF-8.",
        ) from exc

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV is empty or has no header row.")

    headers = {}
    for header in reader.fieldnames:
        normalized = re.sub(r"[^a-z0-9]+", "_", (header or "").strip().casefold()).strip("_")
        field = _CSV_HEADER_FIELDS.get(normalized)
        if field:
            headers.setdefault(field, []).append(header)
    if not any(field in headers for field in ("business_name", "domain", "email", "phone", "maps_url")):
        raise HTTPException(
            status_code=400,
            detail="CSV needs a recognized lead column such as Business Name, Domain, Email, or Phone.",
        )

    records = []
    errors = []
    for line_number, row in enumerate(reader, 2):
        if line_number - 1 > MAX_IMPORT_ROWS:
            raise HTTPException(
                status_code=400,
                detail=f"CSV exceeds the {MAX_IMPORT_ROWS:,}-row import limit.",
            )
        if None in row:
            errors.append(f"Row {line_number}: too many values for the header columns.")
            continue
        email = _csv_value(row, headers, "email")
        if email:
            email = email.lower()
            if not EMAIL_RE.fullmatch(email):
                errors.append(f"Row {line_number}: invalid email address.")
                continue
        website = _csv_value(row, headers, "website")
        domain = _normalize_domain(_csv_value(row, headers, "domain"))
        if not domain and website:
            domain = _normalize_domain(website)
        if not domain and email:
            domain = email.rsplit("@", 1)[1]
        business_name = _csv_value(row, headers, "business_name") or domain
        record = {
            "business_name": business_name,
            "domain": domain,
            "email": email,
            "phone": _csv_value(row, headers, "phone"),
            "website": website,
            "address": _csv_value(row, headers, "address"),
            "city": _csv_value(row, headers, "city"),
            "country": _csv_value(row, headers, "country"),
            "rating": _csv_value(row, headers, "rating"),
            "reviews": _csv_value(row, headers, "reviews"),
            "maps_url": _csv_value(row, headers, "maps_url"),
            "improvement_note": _csv_value(row, headers, "improvement_note"),
            "lead_line": _csv_value(row, headers, "lead_line"),
            "last_review_days": _csv_value(row, headers, "last_review_days"),
            "contact_name": _csv_value(row, headers, "contact_name"),
            "homepage_snippet": _csv_value(row, headers, "homepage_snippet"),
            "notes": _csv_value(row, headers, "notes"),
        }
        if not any(record.get(field) for field in ("business_name", "domain", "email", "phone", "maps_url")):
            if not any(value and str(value).strip() for value in row.values()):
                continue
            errors.append(f"Row {line_number}: no business name, domain, email, phone, or Maps URL.")
            continue
        try:
            record["rating"] = _optional_number(record["rating"], "rating", line_number)
            record["reviews"] = _optional_number(record["reviews"], "reviews", line_number, integer=True)
            record["last_review_days"] = _optional_number(
                record["last_review_days"], "last_review_days", line_number, integer=True,
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue
        records.append(record)
    return records, errors


def _import_lead_records(records, org_id):
    inserted = 0
    skipped = 0
    with db.get_conn() as connection:
        for record in records:
            duplicate = None
            if record["maps_url"]:
                duplicate = connection.execute(
                    "SELECT id FROM leads WHERE org_id=%s AND maps_url=%s LIMIT 1",
                    (org_id, record["maps_url"]),
                ).fetchone()
            if not duplicate and record["email"]:
                duplicate = connection.execute(
                    "SELECT id FROM leads WHERE org_id=%s AND lower(email)=lower(%s) LIMIT 1",
                    (org_id, record["email"]),
                ).fetchone()
            if (
                not duplicate and not record["maps_url"] and not record["email"]
                and record["domain"] and record["business_name"]
            ):
                duplicate = connection.execute(
                    "SELECT id FROM leads WHERE org_id=%s AND lower(domain)=lower(%s) "
                    "AND lower(business_name)=lower(%s) LIMIT 1",
                    (org_id, record["domain"], record["business_name"]),
                ).fetchone()
            if duplicate:
                skipped += 1
                continue

            columns = [
                "org_id", "source", "status", "created_at",
                *record.keys(),
            ]
            values = [
                org_id, "csv_import", "new", db.now(),
                *record.values(),
            ]
            connection.execute(
                f"INSERT INTO leads ({', '.join(columns)}) "
                f"VALUES ({', '.join('%s' for _ in columns)})",
                values,
            )
            inserted += 1
    return inserted, skipped


@router.get("")
def list_leads(
    bucket: str = "all",      # all | call | email | pending | none
    status: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 200,
    offset: int = 0,
    org_id: str = Depends(get_org_id),
):
    """
    Return leads for the authenticated user, segmented by contact readiness.
    Country/city narrow the result to a specific territory (e.g. "UK" + "London")
    so outreach can be targeted per region. Also returns bucket_counts so the
    frontend can render tab badges.
    """
    base_where = "WHERE org_id = %s"
    args: list = [org_id]

    bucket_sql = {
        "call":    "AND phone IS NOT NULL AND phone != ''",
        "email":   "AND email IS NOT NULL AND email != ''",
        "pending": "AND (email IS NULL OR email='') AND domain IS NOT NULL AND domain != ''",
        "none":    "AND (phone IS NULL OR phone='') AND (email IS NULL OR email='') AND (domain IS NULL OR domain='')",
    }

    filter_clause = bucket_sql.get(bucket, "")

    geo_clause = ""
    if country:
        geo_clause += " AND country = %s"
        args.append(country)
    if city:
        geo_clause += " AND city = %s"
        args.append(city)

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
        # Bucket counts (no search/status/geo filter — always show full counts)
        def _count(extra_clause):
            row = c.execute(
                f"SELECT COUNT(*) AS n FROM leads {base_where} {extra_clause}",
                [org_id],
            ).fetchone()
            return row["n"] if row else 0

        bucket_counts = {
            "all":     _count(""),
            "call":    _count(bucket_sql["call"]),
            "email":   _count(bucket_sql["email"]),
            "pending": _count(bucket_sql["pending"]),
            "none":    _count(bucket_sql["none"]),
        }

        # Total matching the *active* filters (bucket + geo + status + search) —
        # used for pagination. bucket_counts above stays filter-free on purpose
        # (tab badges show the full bucket size regardless of search text).
        filtered_total = c.execute(
            f"SELECT COUNT(*) AS n FROM leads {base_where} {filter_clause} {geo_clause} {status_clause} {search_clause}",
            args,
        ).fetchone()["n"]

        # Main query
        rows = c.execute(
            f"""
            SELECT id, source, business_name, domain, email, phone, website,
                   address, city, country, rating, reviews,
                   maps_url, improvement_note, lead_line, last_review_days,
                   status, do_not_contact, created_at
            FROM leads
            {base_where} {filter_clause} {geo_clause} {status_clause} {search_clause}
            ORDER BY id DESC
            LIMIT %s OFFSET %s
            """,
            args + [limit, offset],
        ).fetchall()

    return {
        "items": [dict(r) for r in rows],
        "total": filtered_total,
        "bucket_counts": bucket_counts,
    }


@router.post("/import-csv")
async def import_leads_csv(
    file: UploadFile = File(...),
    org_id: str = Depends(get_org_id),
):
    """Import extracted or exported lead rows into the active organization."""
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Choose a .csv file to import.")
    contents = await file.read(MAX_IMPORT_BYTES + 1)
    if len(contents) > MAX_IMPORT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"CSV exceeds the {MAX_IMPORT_BYTES // (1024 * 1024)} MB upload limit.",
        )
    records, errors = _parse_leads_csv(contents)
    if not records and not errors:
        raise HTTPException(status_code=400, detail="CSV contains no lead rows.")
    imported, skipped = _import_lead_records(records, org_id) if records else (0, 0)
    return {
        "imported": imported,
        "skipped": skipped,
        "invalid": len(errors),
        "errors": errors[:20],
    }


@router.get("/meta/countries")
def lead_countries(org_id: str = Depends(get_org_id)):
    """Distinct countries present in the user's leads (drop-down options)."""
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT DISTINCT country FROM leads "
            "WHERE org_id=%s AND country IS NOT NULL AND country != '' "
            "ORDER BY country",
            [org_id],
        ).fetchall()
    return {"countries": [r["country"] for r in rows]}


@router.get("/meta/cities")
def lead_cities(country: Optional[str] = None, org_id: str = Depends(get_org_id)):
    """Distinct cities for a country (or all) present in the user's leads."""
    if country:
        rows_q = (
            "SELECT DISTINCT city FROM leads "
            "WHERE org_id=%s AND city IS NOT NULL AND city != '' "
            "AND country = %s ORDER BY city"
        )
        args: list = [org_id, country]
    else:
        rows_q = (
            "SELECT DISTINCT city FROM leads "
            "WHERE org_id=%s AND city IS NOT NULL AND city != '' "
            "ORDER BY city"
        )
        args = [org_id]
    with db.get_conn() as c:
        rows = c.execute(rows_q, args).fetchall()
    return {"cities": [r["city"] for r in rows]}


@router.get("/{lead_id}")
def get_lead(lead_id: int, org_id: str = Depends(get_org_id)):
    with db.get_conn() as c:
        row = c.execute(
            "SELECT * FROM leads WHERE id=%s AND org_id=%s",
            (lead_id, org_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")
    return dict(row)


@router.patch("/{lead_id}")
def update_lead(lead_id: int, body: LeadStatusUpdate, org_id: str = Depends(get_org_id)):
    with db.get_conn() as c:
        existing = c.execute(
            "SELECT id FROM leads WHERE id=%s AND org_id=%s",
            (lead_id, org_id),
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


@router.post("/exclude")
def exclude_leads(body: LeadIdsBody, org_id: str = Depends(get_org_id)):
    """Mark selected leads as do-not-contact and cancel all their pending sends."""
    updated = db.set_leads_do_not_contact(body.lead_ids, True, org_id=org_id)
    for lid in body.lead_ids:
        db.cancel_future_sends_for_lead(lid, org_id=org_id)
    return {"ok": True, "updated": updated}


@router.post("/unexclude")
def unexclude_leads(body: LeadIdsBody, org_id: str = Depends(get_org_id)):
    """Clear the do-not-contact flag on selected leads (re-enrollable)."""
    updated = db.set_leads_do_not_contact(body.lead_ids, False, org_id=org_id)
    return {"ok": True, "updated": updated}
