"""
Refresh email templates from outreach.DEFAULT_TEMPLATES.

`seed_default_templates()` only runs on an empty table — once the user has
templates, edits to DEFAULT_TEMPLATES never reach the DB. This script does
the missing piece: match by name and overwrite subject/body.

Run after editing DEFAULT_TEMPLATES:
    python -m scripts.refresh_templates

Missing defaults are inserted only into unassigned steps. Customized templates
and templates the user added manually are left alone.
"""

import db
from outreach import DEFAULT_TEMPLATES


def run():
    db.init_db()
    org_id = db.DEFAULT_ORG_ID
    templates = db.list_templates(org_id=org_id)
    by_step = {
        t["step_number"]: t
        for t in templates
        if t.get("step_number") in range(1, 5)
    }
    by_name = {t["name"]: t for t in templates}

    inserted, updated, skipped = 0, 0, 0
    for t in DEFAULT_TEMPLATES:
        match = by_name.get(t["name"])
        if match:
            if match["subject"] == t["subject"] and match["body"] == t["body"]:
                skipped += 1
                continue
            db.upsert_template(
                t["name"], t["subject"], t["body"], match["id"],
                org_id=org_id,
                step_number=t["step_number"],
            )
            updated += 1
            print(f"  updated step {t['step_number']}: {t['name']}")
        else:
            if t["step_number"] in by_step:
                skipped += 1
                continue
            db.upsert_template(
                t["name"], t["subject"], t["body"],
                org_id=org_id,
                step_number=t["step_number"],
            )
            inserted += 1
            print(f"  inserted step {t['step_number']}: {t['name']}")

    print(f"\nDone. inserted={inserted} updated={updated} unchanged={skipped}")


if __name__ == "__main__":
    run()
