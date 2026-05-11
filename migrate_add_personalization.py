"""
One-shot migration: add homepage_snippet + opener columns to leads.

  homepage_snippet — raw text scraped from the lead's homepage during email
                     extraction. Used as input to the LLM opener generator.
  opener           — one-sentence personalized opener generated lazily on
                     first send. Cached so re-sends never re-pay LLM cost.

Run once:
    python migrate_add_personalization.py
"""

import db

MIGRATIONS = [
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS homepage_snippet TEXT",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS opener TEXT",
]


def run():
    db.init_db()
    print("Running personalization migration...")
    with db.get_conn() as c:
        for sql in MIGRATIONS:
            print(f"  {sql}")
            c.execute(sql)
    print("Done.")


if __name__ == "__main__":
    run()
