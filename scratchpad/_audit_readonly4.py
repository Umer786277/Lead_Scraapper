import json
import db

def j(v):
    return v.isoformat() if hasattr(v, "isoformat") else v

def show(title, rows):
    print(f"\n=== {title} ===")
    for r in rows:
        print(json.dumps({k: j(v) for k, v in dict(r).items()}, default=str))

with db.get_conn() as c:
    show("campaign5 counts", c.execute("SELECT s.status, s.step_number, COUNT(*) n FROM scheduled_sends s WHERE s.campaign_id=5 GROUP BY s.status, s.step_number ORDER BY s.step_number, s.status").fetchall())
    show("campaign5 sent subjects", c.execute("SELECT cs.template_id, t.name tpl_name, t.subject, COUNT(*) n FROM scheduled_sends s JOIN campaign_steps cs ON cs.id=s.step_id LEFT JOIN email_templates t ON t.id=cs.template_id WHERE s.campaign_id=5 AND s.status='sent' GROUP BY cs.template_id, t.name, t.subject ORDER BY cs.template_id").fetchall())
    show("outbound 1004/1005/1006/1008", c.execute("SELECT o.id, o.lead_id, o.status, o.attempts, o.next_attempt_at, o.sent_at, o.last_error, o.account_id FROM outbound_messages o WHERE o.lead_id IN (1004,1005,1006,1008) ORDER BY o.id").fetchall())
    show("campaign6 leads", c.execute("SELECT id, business_name, email, status FROM leads WHERE id IN (1050,1051,1057,1069) ORDER BY id").fetchall())
    show("campaign5 step2 next due", c.execute("SELECT MIN(scheduled_at) next_at, COUNT(*) n FROM scheduled_sends WHERE campaign_id=5 AND status='pending' AND step_number=2").fetchall())
