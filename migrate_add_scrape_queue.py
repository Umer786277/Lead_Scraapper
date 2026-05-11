"""
Migration: scrape_queue (saturation-aware city rotation).

Two tables:

  scrape_schedules — user "interest" rows. Each schedule registers that
                     user_X wants ongoing scraping for niche=Y in country=Z.

  scrape_queries   — actual units of work, keyed by (niche, city, country).
                     Shared across users. The rotation worker picks the
                     laggard query (oldest scrape, fewest leads) on each
                     hourly tick.

Run once:
    python migrate_add_scrape_queue.py
"""

import db

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS scrape_schedules (
        id            SERIAL PRIMARY KEY,
        user_id       TEXT NOT NULL,
        niche         TEXT NOT NULL,
        country       TEXT NOT NULL,
        target_leads  INTEGER DEFAULT 20,
        status        TEXT DEFAULT 'active',
        created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS scrape_queries (
        id              SERIAL PRIMARY KEY,
        niche           TEXT NOT NULL,
        city            TEXT NOT NULL,
        country         TEXT NOT NULL,
        lead_count      INTEGER DEFAULT 0,
        dedup_rate      REAL,
        last_scraped_at TIMESTAMP,
        next_run_at     TIMESTAMP,
        last_error      TEXT,
        runs_total      INTEGER DEFAULT 0,
        UNIQUE(niche, city, country)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scrape_queries_due "
    "ON scrape_queries(next_run_at NULLS FIRST, last_scraped_at NULLS FIRST, lead_count ASC)",
    "CREATE INDEX IF NOT EXISTS idx_scrape_schedules_user "
    "ON scrape_schedules(user_id, status)",
    # LLM-fetched city cache for countries not in the static cities.py map.
    """
    CREATE TABLE IF NOT EXISTS country_cities (
        country     TEXT PRIMARY KEY,
        cities      TEXT NOT NULL,
        fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]


def run():
    db.init_db()
    print("Running scrape_queue migration...")
    with db.get_conn() as c:
        for sql in MIGRATIONS:
            print(f"  {' '.join(sql.split())[:80]}")
            c.execute(sql)
    print("Done.")


if __name__ == "__main__":
    run()
