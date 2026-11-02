from dotenv import load_dotenv
load_dotenv(override=True)

import db
import worker_replies

db.init_db()
db.worker_heartbeat_write(111, "2026-08-30T14:00:00",
                          {"enrich": {"at": "2026-08-30T14:05:00"},
                           "send": {"at": "2026-08-30T14:05:00"}})
db.worker_heartbeat_write(222, "2026-08-30T14:00:00",
                          {"inbox": {"at": "2026-08-30T14:07:00"},
                           "booking_replies": {"at": "2026-08-30T14:07:00"}})
m = db.worker_heartbeat_read()
need = {"enrich", "send", "inbox", "booking_replies"}
assert need <= set(m["jobs"]), f"merged jobs missing: {need - set(m['jobs'])}"
print("ok  heartbeat merge keeps both workers' jobs:", sorted(m["jobs"]))
print("ok  shared job fns:", worker_replies.job_inbox.__name__,
      worker_replies.job_booking_replies.__name__)