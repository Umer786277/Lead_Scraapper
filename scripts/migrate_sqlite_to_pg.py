"""
One-shot migration: SQLite (data/leads.db) → Postgres (DATABASE_URL).

Usage:
    python migrate_sqlite_to_pg.py                    # do it
    python migrate_sqlite_to_pg.py --dry-run          # just show counts
    python migrate_sqlite_to_pg.py --sqlite other.db  # specify source

- Safe to re-run: uses ON CONFLICT DO NOTHING.
- Preserves row IDs so foreign keys remain valid.
- Resets PG sequences to max(id) at the end so new inserts don't collide.
- Migrates in FK-safe order.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

import db  # PG helper — pulls DATABASE_URL from .env

# (table_name, ordered column list) — FK-safe order
TABLES_IN_ORDER = [
    ("extraction_runs",      ["id", "run_type", "started_at", "finished_at", "status",
                              "input_count", "output_count", "metadata"]),
    ("email_templates",      ["id", "name", "subject", "body", "created_at", "updated_at"]),
    ("domains",              ["id", "run_id", "domain", "status", "has_mx",
                              "pages_visited", "emails_found", "error", "scraped_at"]),
    ("emails",               ["id", "domain_id", "domain", "email", "source_url",
                              "source_type", "confidence", "is_role",
                              "verification_status", "extracted_at"]),
    ("leads",                ["id", "source", "run_id", "business_name", "domain",
                              "email", "phone", "website", "address", "city", "country",
                              "rating", "reviews", "status", "notes", "created_at"]),
    ("campaigns",            ["id", "name", "status", "leads_count", "created_at", "notes"]),
    ("campaign_steps",       ["id", "campaign_id", "step_number", "delay_days", "template_id"]),
    ("scheduled_sends",      ["id", "campaign_id", "step_id", "step_number", "lead_id",
                              "scheduled_at", "status", "sent_at", "error"]),
    ("email_events",         ["id", "lead_id", "campaign_id", "event_type", "subject",
                              "message_id", "note", "created_at"]),
    ("vendor_threads",       ["id", "lead_id", "subject", "status", "price_floor",
                              "currency", "goal", "last_inbound_at", "created_at"]),
    ("negotiation_messages", ["id", "thread_id", "direction", "subject", "body",
                              "message_id", "in_reply_to", "from_addr", "to_addr",
                              "created_at"]),
    ("negotiation_drafts",   ["id", "thread_id", "model", "draft_subject", "draft_body",
                              "detected_price", "detected_currency", "reasoning",
                              "suggested_action", "status", "created_at"]),
    ("deals",                ["id", "lead_id", "thread_id", "vendor_name", "final_price",
                              "currency", "terms", "notes", "closed_at"]),
]


def _sqlite_has_table(conn, table):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _migrate_table(sconn, pconn, table, cols, dry_run):
    if not _sqlite_has_table(sconn, table):
        return 0, 0, "missing in SQLite"

    rows = sconn.execute(f"SELECT {','.join(cols)} FROM {table}").fetchall()
    if not rows:
        return 0, 0, "empty"

    if dry_run:
        return len(rows), 0, "dry-run"

    placeholders = ", ".join("%s" for _ in cols)
    col_list = ", ".join(cols)
    sql = (f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
           f"ON CONFLICT (id) DO NOTHING")

    inserted = 0
    with pconn.cursor() as cur:
        for r in rows:
            cur.execute(sql, tuple(r[c] for c in cols))
            if cur.rowcount > 0:
                inserted += 1
    return len(rows), inserted, "ok"


def _reset_sequences(pconn):
    """Bump each table's id sequence to max(id), so new SERIAL inserts don't collide."""
    with pconn.cursor() as cur:
        for table, _ in TABLES_IN_ORDER:
            cur.execute(
                f"SELECT setval("
                f"  pg_get_serial_sequence('{table}', 'id'),"
                f"  COALESCE((SELECT MAX(id) FROM {table}), 1),"
                f"  (SELECT MAX(id) IS NOT NULL FROM {table})"
                f")"
            )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sqlite", default=str(Path(__file__).parent / "data" / "leads.db"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    src = Path(args.sqlite)
    if not src.exists():
        print(f"❌  No SQLite DB at {src}")
        print("    Nothing to migrate — tables will be auto-created on first app run.")
        return

    print(f"Source SQLite : {src}")
    print(f"Target        : Postgres via DATABASE_URL")
    if args.dry_run:
        print("Mode          : DRY-RUN (no writes)")
    print()

    # Ensure PG tables exist first
    try:
        db.init_db()
    except Exception as e:
        print(f"❌  Couldn't initialize Postgres schema: {e}")
        sys.exit(1)
    print("✅  Postgres schema ready\n")

    sconn = sqlite3.connect(str(src))
    sconn.row_factory = sqlite3.Row

    total_src = 0
    total_inserted = 0
    try:
        with db.get_conn() as pconn:
            for table, cols in TABLES_IN_ORDER:
                try:
                    src_n, ins, note = _migrate_table(sconn, pconn, table, cols, args.dry_run)
                except Exception as e:
                    print(f"  {table:28s}  ❌  {e}")
                    raise
                total_src += src_n
                total_inserted += ins
                tag = f"src={src_n:>6}  inserted={ins:>6}  [{note}]"
                print(f"  {table:28s}  {tag}")

            if not args.dry_run:
                _reset_sequences(pconn)
                print("\n✅  Sequences reset to max(id)")
    finally:
        sconn.close()

    print(f"\nScanned {total_src} rows, inserted {total_inserted} new.")


if __name__ == "__main__":
    main()
