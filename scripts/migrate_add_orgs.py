"""
One-shot migration: organizations + memberships + org_id backfill.

Run once after pulling this branch:
    python -m scripts.migrate_add_orgs

`db.init_db()` creates the org tables, adds every `org_id` column (with
DEFAULT so existing rows auto-backfill), grants existing data to the default
org, and swaps the old global unique constraints for per-org ones. This script
then creates a membership row for every user_id it can find so existing users
land in the default org instead of getting a fresh empty personal one.

Safe to run multiple times.
"""

import uuid

import db


def _user_id_columns(c):
    rows = c.execute(
        "SELECT table_name FROM information_schema.columns "
        "WHERE column_name='user_id' AND table_schema='public'"
    ).fetchall()
    return [r["table_name"] for r in rows]


def run():
    db.init_db()
    print(f"Default org: {db.DEFAULT_ORG_ID}")

    with db.get_conn() as c:
        user_ids = set()
        for table in _user_id_columns(c):
            try:
                rows = c.execute(
                    f"SELECT DISTINCT user_id FROM {table} WHERE user_id IS NOT NULL"
                ).fetchall()
            except Exception:
                continue
            user_ids.update(r["user_id"] for r in rows if r["user_id"])

        for uid in sorted(str(u) for u in user_ids):
            try:
                uuid.UUID(uid)
            except ValueError:
                print(f"  WARNING: skipping non-UUID user_id={uid!r}")
                continue
            try:
                c.execute("SAVEPOINT backfill_user")
                c.execute(
                    "INSERT INTO memberships (user_id, org_id, role, created_at) "
                    "VALUES (%s, %s, 'owner', %s) ON CONFLICT DO NOTHING",
                    (uid, db.DEFAULT_ORG_ID, db.now()),
                )
                c.execute("RELEASE SAVEPOINT backfill_user")
            except Exception as e:
                c.execute("ROLLBACK TO SAVEPOINT backfill_user")
                print(f"  WARNING: could not backfill user_id={uid!r}: {e}")

        print(f"Backfilled {len(user_ids)} membership(s) into the default org.")

        for table in ("leads", "emails", "domains", "campaigns",
                      "email_templates", "scheduled_sends", "email_events",
                      "vendor_threads", "pending_replies", "calls",
                      "scrape_schedules", "scrape_queries", "deals"):
            try:
                row = c.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE org_id IS NULL"
                ).fetchone()
                if row and row["n"]:
                    print(f"  WARNING: {row['n']} row(s) in {table} have NULL org_id")
            except Exception:
                pass

    print("Done.")


if __name__ == "__main__":
    run()
