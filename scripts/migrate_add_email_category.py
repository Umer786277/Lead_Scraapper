"""
One-shot migration: add category column to emails table.

Categories: personal_named | personal | role | pattern_guess
Used to score reply-likelihood when picking the "best" email per domain.

Run once:
    python migrate_add_email_category.py

Safe to run multiple times.
"""

import db

MIGRATIONS = [
    "ALTER TABLE emails ADD COLUMN IF NOT EXISTS category TEXT",
    "CREATE INDEX IF NOT EXISTS idx_emails_category ON emails(category)",
]

# Backfill existing rows from is_role + source_type (best-effort).
BACKFILL = """
UPDATE emails
SET category = CASE
    WHEN source_type = 'pattern_guess' THEN 'pattern_guess'
    WHEN is_role = 1 THEN 'role'
    WHEN split_part(email, '@', 1) ~* '^[a-z]{2,}[._-][a-z]{2,}' THEN 'personal_named'
    ELSE 'personal'
END
WHERE category IS NULL
"""


def run():
    db.init_db()
    print("Running email category migration...")
    with db.get_conn() as c:
        for sql in MIGRATIONS:
            print(f"  {sql[:70]}")
            c.execute(sql)
        print("  backfilling category for existing rows...")
        c.execute(BACKFILL)
    print("Done.")


if __name__ == "__main__":
    run()
