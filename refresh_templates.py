"""
Refresh email templates from outreach.DEFAULT_TEMPLATES.

`seed_default_templates()` only runs on an empty table — once the user has
templates, edits to DEFAULT_TEMPLATES never reach the DB. This script does
the missing piece: match by name and overwrite subject/body.

Run after editing DEFAULT_TEMPLATES:
    python refresh_templates.py

Templates not present in the DB are inserted.
Templates the user added manually (no matching name) are left alone.
"""

import db
from outreach import DEFAULT_TEMPLATES


def run():
    db.init_db()
    existing = {t["name"]: t for t in db.list_templates()}

    inserted, updated, skipped = 0, 0, 0
    for t in DEFAULT_TEMPLATES:
        match = existing.get(t["name"])
        if match:
            if match["subject"] == t["subject"] and match["body"] == t["body"]:
                skipped += 1
                continue
            db.upsert_template(t["name"], t["subject"], t["body"], match["id"])
            updated += 1
            print(f"  updated: {t['name']}")
        else:
            db.upsert_template(t["name"], t["subject"], t["body"])
            inserted += 1
            print(f"  inserted: {t['name']}")

    print(f"\nDone. inserted={inserted} updated={updated} unchanged={skipped}")


if __name__ == "__main__":
    run()
