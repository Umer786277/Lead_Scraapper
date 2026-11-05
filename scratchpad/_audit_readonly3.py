import json
import db

def j(v):
    return v.isoformat() if hasattr(v, "isoformat") else v

def show(title, rows):
    print(f"\n=== {title} ===")
    for r in rows:
        print(json.dumps({k: j(v) for k, v in dict(r).items()}, default=str))

with db.get_conn() as c:
    show("accounts", c.execute("SELECT id, org_id, from_email, status, daily_limit, hourly_limit, warmup_started_at, warmup_days FROM email_accounts ORDER BY id").fetchall())
    show("sent per account", c.execute("SELECT account_id, event_type, COUNT(*) n, MIN(created_at) first_at, MAX(created_at) last_at FROM email_events WHERE event_type IN ('sent','booking_sent') GROUP BY account_id, event_type ORDER BY account_id").fetchall())
    show("costrucion lead sends", c.execute("SELECT s.id send_id, s.lead_id, s.step_number, s.status, s.scheduled_at, s.sent_at, s.error, o.id outbound_id, o.status outbound_status, o.attempts, o.next_attempt_at, o.idempotency_key FROM scheduled_sends s LEFT JOIN outbound_messages o ON o.send_id=s.id WHERE s.campaign_id=6 ORDER BY s.lead_id, s.step_number").fetchall())
    show("liverpool 991-1012", c.execute("SELECT l.id, l.business_name, l.email, l.status lead_status, s.step_number, s.status send_status, s.scheduled_at, s.sent_at, cs.template_id, t.name tpl_name, t.subject FROM leads l JOIN scheduled_sends s ON s.lead_id=l.id JOIN campaign_steps cs ON cs.id=s.step_id LEFT JOIN email_templates t ON t.id=cs.template_id WHERE l.id BETWEEN 991 AND 1012 ORDER BY l.id, s.step_number").fetchall())
    show("liverpool pending", c.execute("SELECT s.lead_id, s.step_number, s.id, s.status, s.scheduled_at, cs.template_id, t.name tpl_name, t.subject FROM scheduled_sends s JOIN campaign_steps cs ON cs.id=s.step_id LEFT JOIN email_templates t ON t.id=cs.template_id WHERE s.campaign_id=5 AND s.status IN ('pending','sending') ORDER BY s.lead_id, s.step_number").fetchall())
