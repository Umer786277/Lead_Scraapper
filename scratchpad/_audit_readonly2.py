import json

import db


def j(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def show(title, rows):
    print(f"\n=== {title} ===")
    for row in rows:
        print(json.dumps({k: j(v) for k, v in dict(row).items()}, default=str))


with db.get_conn() as c:
    show(
        "campaigns summary",
        c.execute(
            "SELECT id, name, status, sequence_id, script_set_id, "
            "script_rotation_index, script_rotation_order, "
            "(SELECT string_agg(v->>'name', ', ') FROM jsonb_array_elements("
            "COALESCE(script_variants,'[]'::jsonb)) v) AS variant_names, "
            "leads_count, created_at FROM campaigns ORDER BY id"
        ).fetchall(),
    )

    show(
        "campaign 5 steps",
        c.execute(
            "SELECT cs.id, cs.campaign_id, cs.step_number, cs.delay_days, "
            "cs.template_id, cs.template_name, cs.subject, "
            "t.id AS live_template_id, t.name AS live_template_name, "
            "t.subject AS live_subject, left(t.body, 80) AS live_body "
            "FROM campaign_steps cs LEFT JOIN email_templates t "
            "ON t.id=cs.template_id WHERE cs.campaign_id=5 "
            "ORDER BY cs.step_number"
        ).fetchall(),
    )

    show(
        "campaign 6 steps",
        c.execute(
            "SELECT cs.id, cs.campaign_id, cs.step_number, cs.delay_days, "
            "cs.template_id, cs.template_name, cs.subject "
            "FROM campaign_steps cs WHERE cs.campaign_id=6 "
            "ORDER BY cs.step_number"
        ).fetchall(),
    )

    show(
        "email templates",
        c.execute(
            "SELECT id, org_id, name, subject, left(body, 60) AS body "
            "FROM email_templates ORDER BY id"
        ).fetchall(),
    )

    show(
        "sequences",
        c.execute(
            "SELECT id, org_id, name FROM outreach_sequences ORDER BY id"
        ).fetchall(),
    )

    show(
        "sequence steps",
        c.execute(
            "SELECT ss.sequence_id, ss.step_number, ss.delay_days, "
            "ss.template_id, t.name AS template_name, t.subject "
            "FROM outreach_sequence_steps ss LEFT JOIN email_templates t "
            "ON t.id=ss.template_id ORDER BY ss.sequence_id, ss.step_number"
        ).fetchall(),
    )

    show(
        "email accounts",
        c.execute(
            "SELECT id, org_id, email, status, daily_limit, hourly_limit, "
            "sent_today, sent_this_hour, last_sent_at, warmup_enabled, "
            "warmup_started_at FROM email_accounts ORDER BY id"
        ).fetchall(),
    )

    show(
        "org hourly limits",
        c.execute(
            "SELECT id, name, hourly_allocation_limit FROM organizations ORDER BY id"
        ).fetchall(),
    )

    show(
        "lead status summary",
        c.execute(
            "SELECT status, COUNT(*) AS n FROM leads GROUP BY status ORDER BY status"
        ).fetchall(),
    )
