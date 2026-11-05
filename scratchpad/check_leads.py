import json
import db

lead_ids = [994, 998, 999, 1004, 1009, 1010, 1011]

with db.get_conn() as c:
    print('=== LEADS TABLE ===')
    leads = c.execute(
        'SELECT id, business_name, email, status, website, phone '
        'FROM leads WHERE id = ANY(%s) ORDER BY id',
        (lead_ids,)
    ).fetchall()
    for l in leads:
        print(dict(l))

    print('\n=== CAMPAIGN_SCRIPT_ASSIGNMENTS ===')
    assignments = c.execute(
        'SELECT campaign_id, lead_id, script_name, script_snapshot '
        'FROM campaign_script_assignments WHERE lead_id = ANY(%s) ORDER BY lead_id',
        (lead_ids,)
    ).fetchall()
    for a in assignments:
        row = dict(a)
        snap = row.get('script_snapshot') or {}
        steps = snap.get('steps', []) if isinstance(snap, dict) else []
        subjects = [s.get('subject') for s in steps]
        print(f"lead {row['lead_id']}: campaign_id={row['campaign_id']} script_name={row['script_name']} step_count={len(steps)} subjects={subjects}")

    print('\n=== SCHEDULED_SENDS (CAMPAIGN 5) ===')
    sends = c.execute(
        'SELECT id, lead_id, step_number, status, scheduled_at, sent_at, error, script_name '
        'FROM scheduled_sends WHERE campaign_id=5 AND lead_id = ANY(%s) ORDER BY lead_id, step_number',
        (lead_ids,)
    ).fetchall()
    for s in sends:
        print(dict(s))

    print('\n=== OUTBOUND_MESSAGES (CAMPAIGN 5) ===')
    outbound = c.execute(
        'SELECT id, lead_id, send_id, to_email, subject, status, attempts, '
        'next_attempt_at, lease_expires_at, last_error, sent_at, created_at '
        'FROM outbound_messages WHERE campaign_id=5 AND lead_id = ANY(%s) ORDER BY lead_id, id',
        (lead_ids,)
    ).fetchall()
    for o in outbound:
        print(dict(o))

    print('\n=== EMAIL_EVENTS ===')
    events = c.execute(
        'SELECT id, account_id, lead_id, event_type, created_at '
        'FROM email_events WHERE lead_id = ANY(%s) ORDER BY lead_id, id',
        (lead_ids,)
    ).fetchall()
    for e in events:
        print(dict(e))
