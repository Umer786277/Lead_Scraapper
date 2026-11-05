import json
from datetime import datetime, timezone
from pathlib import Path

import db


OUT = Path(__file__).resolve().parent


def j(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def rows(cur):
    return [{k: j(v) for k, v in dict(r).items()} for r in cur.fetchall()]


with db.get_conn() as c:
    campaign = rows(c.execute(
        "SELECT id, org_id, name, status, leads_count FROM campaigns WHERE id=5"
    ))

    counts = rows(c.execute(
        "SELECT status, step_number, COUNT(*) AS n "
        "FROM scheduled_sends WHERE campaign_id=5 "
        "GROUP BY status, step_number ORDER BY step_number, status"
    ))

    step1_queued = rows(c.execute(
        "SELECT s.id AS send_id, s.lead_id, l.business_name, l.email, "
        "       s.step_number, s.status, s.scheduled_at, s.claimed_at "
        "FROM scheduled_sends s JOIN leads l ON l.id=s.lead_id "
        "WHERE s.campaign_id=5 AND s.step_number=1 AND s.status='queued' "
        "ORDER BY s.lead_id, s.id"
    ))

    step1_sent = rows(c.execute(
        "SELECT s.id AS send_id, s.lead_id, l.business_name, l.email, "
        "       s.step_number, s.status, s.scheduled_at, s.sent_at "
        "FROM scheduled_sends s JOIN leads l ON l.id=s.lead_id "
        "WHERE s.campaign_id=5 AND s.step_number=1 AND s.status='sent' "
        "ORDER BY s.lead_id, s.id"
    ))

    sent_followups = rows(c.execute(
        "SELECT s.id AS send_id, s.lead_id, l.business_name, l.email, "
        "       s.step_number, s.status, s.scheduled_at "
        "FROM scheduled_sends s JOIN leads l ON l.id=s.lead_id "
        "WHERE s.campaign_id=5 AND s.status='pending' AND s.lead_id IN ("
        "  SELECT lead_id FROM scheduled_sends "
        "  WHERE campaign_id=5 AND step_number=1 AND status='sent'"
        ") ORDER BY s.lead_id, s.step_number, s.id"
    ))

    unsent_followups = rows(c.execute(
        "SELECT s.id AS send_id, s.lead_id, l.business_name, l.email, "
        "       s.step_number, s.status, s.scheduled_at "
        "FROM scheduled_sends s JOIN leads l ON l.id=s.lead_id "
        "WHERE s.campaign_id=5 AND s.status='pending' AND s.lead_id IN ("
        "  SELECT lead_id FROM scheduled_sends "
        "  WHERE campaign_id=5 AND step_number=1 AND status='queued'"
        ") ORDER BY s.lead_id, s.step_number, s.id"
    ))

    outbound_counts = rows(c.execute(
        "SELECT status, COUNT(*) AS n FROM outbound_messages "
        "WHERE campaign_id=5 GROUP BY status ORDER BY status"
    ))

    outbound_open = rows(c.execute(
        "SELECT id, lead_id, status, to_email, subject, attempts, "
        "       next_attempt_at, lease_expires_at, created_at, sent_at "
        "FROM outbound_messages "
        "WHERE campaign_id=5 AND status IN ('pending','retry','sending','queued') "
        "ORDER BY id"
    ))

    outbound_all = rows(c.execute(
        "SELECT id, lead_id, status, to_email, subject, attempts, "
        "       next_attempt_at, sent_at, created_at "
        "FROM outbound_messages WHERE campaign_id=5 ORDER BY id"
    ))

    unknown_12_15 = rows(c.execute(
        "SELECT o.id, o.lead_id, l.business_name, o.to_email, o.status, "
        "       o.attempts, o.next_attempt_at, o.created_at, o.sent_at, "
        "       o.last_error, o.campaign_id, o.send_id "
        "FROM outbound_messages o LEFT JOIN leads l ON l.id=o.lead_id "
        "WHERE o.id BETWEEN 12 AND 15 ORDER BY o.id"
    ))

payload = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "campaign": campaign,
    "scheduled_counts": counts,
    "step1_queued": step1_queued,
    "step1_sent": step1_sent,
    "pending_followups_of_sent_leads": sent_followups,
    "pending_followups_of_queued_leads": unsent_followups,
    "outbound_counts": outbound_counts,
    "outbound_open": outbound_open,
    "outbound_all": outbound_all,
    "unknown_outbound_12_15": unknown_12_15,
}

path = OUT / "phase1_campaign5_snapshot.json"
path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

print(f"snapshot={path}")
print(f"campaign={campaign}")
print(f"scheduled_counts={counts}")
print(f"step1_queued={len(step1_queued)} lead_ids={[r['lead_id'] for r in step1_queued]}")
print(f"step1_sent={len(step1_sent)} lead_ids={[r['lead_id'] for r in step1_sent]}")
print(f"pending_followups_of_sent_leads={len(sent_followups)} "
      f"row_ids={[r['send_id'] for r in sent_followups]}")
print(f"pending_followups_of_queued_leads={len(unsent_followups)} "
      f"row_ids={[r['send_id'] for r in unsent_followups]}")
print(f"outbound_counts={outbound_counts}")
print(f"outbound_open={len(outbound_open)} ids={[r['id'] for r in outbound_open]}")
for r in unknown_12_15:
    print("unknown_12_15=" + json.dumps(r))
