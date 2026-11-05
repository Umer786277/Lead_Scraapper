import json

import db


def j(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return value
    return value


def show(title, rows):
    print(f"\n=== {title} ===")
    for row in rows:
        print(json.dumps({k: j(v) for k, v in dict(row).items()}, default=str))


with db.get_conn() as c:
    show(
        "campaigns",
        c.execute(
            "SELECT id, org_id, name, status, sequence_id, script_set_id, "
            "script_rotation_index, script_rotation_order, "
            "jsonb_array_length(COALESCE(script_variants,'[]'::jsonb)) AS variant_count, "
            "(SELECT string_agg(v->>'name', ', ') FROM jsonb_array_elements("
            "COALESCE(script_variants,'[]'::jsonb)) v) AS variant_names, "
            "leads_count, created_at FROM campaigns ORDER BY id"
        ).fetchall(),
    )

    show(
        "campaign_steps",
        c.execute(
            "SELECT campaign_id, step_number, delay_days, template_id, "
            "template_name, subject FROM campaign_steps "
            "ORDER BY campaign_id, step_number"
        ).fetchall(),
    )

    show(
        "script_sets",
        c.execute(
            "SELECT id, org_id, name, step_delays, "
            "(SELECT string_agg(v->>'name', ', ') FROM jsonb_array_elements(scripts) v) "
            "AS variant_names, "
            "(SELECT jsonb_agg(jsonb_build_array(v->>'name', "
            "(SELECT string_agg(s->>'subject', ' | ') FROM jsonb_array_elements(v->'steps') s))) "
            "FROM jsonb_array_elements(scripts) v) AS subjects "
            "FROM outreach_script_sets ORDER BY id"
        ).fetchall(),
    )

    show(
        "outreach_settings",
        c.execute(
            "SELECT org_id, key, value FROM outreach_settings "
            "WHERE key IN ('campaign_script_selection_mode') ORDER BY org_id"
        ).fetchall(),
    )

    show(
        "liverpool scheduled_sends",
        c.execute(
            "SELECT s.id, s.campaign_id, c.name AS campaign_name, s.lead_id, "
            "l.business_name, s.step_number, s.status, s.scheduled_at, s.sent_at, "
            "s.script_name, cs.template_name, cs.subject "
            "FROM scheduled_sends s "
            "JOIN leads l ON l.id=s.lead_id "
            "JOIN campaigns c ON c.id=s.campaign_id "
            "JOIN campaign_steps cs ON cs.id=s.step_id "
            "WHERE s.lead_id BETWEEN 990 AND 1020 ORDER BY s.lead_id, s.step_number"
        ).fetchall(),
    )

    show(
        "construction scheduled_sends",
        c.execute(
            "SELECT s.id, s.campaign_id, c.name AS campaign_name, s.lead_id, "
            "l.business_name, s.step_number, s.status, s.scheduled_at, s.sent_at, "
            "s.script_name, cs.template_name, cs.subject "
            "FROM scheduled_sends s "
            "JOIN leads l ON l.id=s.lead_id "
            "JOIN campaigns c ON c.id=s.campaign_id "
            "JOIN campaign_steps cs ON cs.id=s.step_id "
            "WHERE s.lead_id IN (1050,1051,1057) ORDER BY s.lead_id, s.step_number"
        ).fetchall(),
    )

    show(
        "campaign_script_assignments",
        c.execute(
            "SELECT a.campaign_id, a.lead_id, l.business_name, a.script_name, "
            "(SELECT string_agg(st->>'subject', ' | ') FROM jsonb_array_elements("
            "a.script_snapshot->'steps') st) AS subjects "
            "FROM campaign_script_assignments a LEFT JOIN leads l ON l.id=a.lead_id "
            "WHERE a.lead_id BETWEEN 990 AND 1060 ORDER BY a.lead_id"
        ).fetchall(),
    )

    show(
        "outbound recent",
        c.execute(
            "SELECT id, source, lead_id, send_id, campaign_id, status, attempts, "
            "to_email, subject, created_at, next_attempt_at, sent_at, last_error, note "
            "FROM outbound_messages ORDER BY id DESC LIMIT 45"
        ).fetchall(),
    )

    show(
        "lead statuses",
        c.execute(
            "SELECT id, business_name, status, email, next_send_at, do_not_contact "
            "FROM leads WHERE id BETWEEN 990 AND 1060 ORDER BY id"
        ).fetchall(),
    )
