"""
One-shot migration: add user_id column to multi-tenant tables.

Run once after pulling this branch:
    python migrate_add_user_id.py

Safe to run multiple times (ALTER TABLE ... IF NOT EXISTS).
"""

import db

MIGRATIONS = [
    # Add user_id to leads (nullable — existing rows keep NULL = "any user can see")
    "ALTER TABLE leads     ADD COLUMN IF NOT EXISTS user_id TEXT",
    "ALTER TABLE campaigns ADD COLUMN IF NOT EXISTS user_id TEXT",
    "ALTER TABLE domains   ADD COLUMN IF NOT EXISTS user_id TEXT",
    "ALTER TABLE emails    ADD COLUMN IF NOT EXISTS user_id TEXT",

    # Indexes for fast per-user queries
    "CREATE INDEX IF NOT EXISTS idx_leads_user_id     ON leads(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_campaigns_user_id ON campaigns(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_domains_user_id   ON domains(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_emails_user_id    ON emails(user_id)",
]


def run():
    db.init_db()
    print("Running user_id migration…")
    with db.get_conn() as c:
        for sql in MIGRATIONS:
            print(f"  {sql[:60]}…")
            c.execute(sql)
    print("Done. All tables have user_id column + index.")


if __name__ == "__main__":
    run()
