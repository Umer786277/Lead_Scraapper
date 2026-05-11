"""
Migration: add contact_name column to leads.

Captured by the LLM opener call when a real person's name appears on the
business's homepage (e.g., "Owner: Sarah Smith", "Dr. John Patel"). Used
as the first fallback for {{first_name}} in email templates.

Run once:
    python migrate_add_contact_name.py
"""

import db

MIGRATIONS = [
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_name TEXT",
]


def run():
    db.init_db()
    print("Adding leads.contact_name...")
    with db.get_conn() as c:
        for sql in MIGRATIONS:
            print(f"  {sql}")
            c.execute(sql)
    print("Done.")


if __name__ == "__main__":
    run()
