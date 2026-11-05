import json

import db


EXPECTED_STEP1 = 7
EXPECTED_FOLLOWUPS = 123
EXPECTED_OUTBOUND = 7


def rows(cur):
    return [dict(row) for row in cur.fetchall()]


with db.get_conn() as c:
    campaign = c.execute(
        "SELECT id, name, status FROM campaigns WHERE id=5 FOR UPDATE"
    ).fetchone()
    if not campaign:
        raise RuntimeError("Campaign 5 not found; rolling back.")

    pre_step1 = c.execute(
        "SELECT COUNT(*) AS n FROM scheduled_sends "
        "WHERE campaign_id=5 AND step_number=1 AND status='queued'"
    ).fetchone()["n"]
    pre_followups = c.execute(
        "SELECT COUNT(*) AS n FROM scheduled_sends "
        "WHERE campaign_id=5 AND status='pending'"
    ).fetchone()["n"]
    pre_outbound = c.execute(
        "SELECT COUNT(*) AS n FROM outbound_messages "
        "WHERE campaign_id=5 AND ("
        "  status IN ('pending','retry') "
        "  OR (status='sending' AND lease_expires_at < clock_timestamp())"
        ")"
    ).fetchone()["n"]
    unexpired_sending = c.execute(
        "SELECT COUNT(*) AS n FROM outbound_messages "
        "WHERE campaign_id=5 AND status='sending' "
        "AND (lease_expires_at IS NULL OR lease_expires_at >= clock_timestamp())"
    ).fetchone()["n"]

    print(
        f"preflight campaign={dict(campaign)} step1={pre_step1} "
        f"followups={pre_followups} outbound={pre_outbound} "
        f"unexpired_sending={unexpired_sending}"
    )
    if (
        pre_step1 != EXPECTED_STEP1
        or pre_followups != EXPECTED_FOLLOWUPS
        or pre_outbound != EXPECTED_OUTBOUND
        or unexpired_sending != 0
    ):
        raise RuntimeError("Preflight counts differ; rolling back.")

    c.execute(
        "UPDATE campaigns SET status='paused' WHERE id=5"
    )

    step1_rows = rows(c.execute(
        "UPDATE scheduled_sends SET status='cancelled' "
        "WHERE campaign_id=5 AND step_number=1 AND status='queued' "
        "RETURNING id, lead_id"
    ))
    followup_rows = rows(c.execute(
        "UPDATE scheduled_sends SET status='cancelled' "
        "WHERE campaign_id=5 AND status='pending' "
        "RETURNING id, lead_id, step_number"
    ))
    outbound_rows = rows(c.execute(
        "UPDATE outbound_messages SET status='cancelled', locked_by=NULL, "
        "lease_expires_at=NULL, "
        "last_error='campaign 5 paused/cancelled (wrong step order)' "
        "WHERE campaign_id=5 AND ("
        "  status IN ('pending','retry') "
        "  OR (status='sending' AND lease_expires_at < clock_timestamp())"
        ") "
        "RETURNING id, lead_id, to_email, status"
    ))

    print(f"cancelled_step1={len(step1_rows)} ids={[r['lead_id'] for r in step1_rows]}")
    print(
        f"cancelled_followups={len(followup_rows)} "
        f"by_step={ {n: sum(1 for r in followup_rows if r['step_number'] == n) for n in (2, 3, 4)} }"
    )
    print(f"cancelled_outbound={len(outbound_rows)} ids={[r['id'] for r in outbound_rows]}")
    print("cancelled_outbound_rows=" + json.dumps(outbound_rows, default=str))

    if (
        len(step1_rows) != EXPECTED_STEP1
        or len(followup_rows) != EXPECTED_FOLLOWUPS
        or len(outbound_rows) != EXPECTED_OUTBOUND
    ):
        raise RuntimeError("Returned counts differ; rolling back.")

    remaining_scheduled = c.execute(
        "SELECT status, COUNT(*) AS n FROM scheduled_sends "
        "WHERE campaign_id=5 AND status IN ('pending','queued') "
        "GROUP BY status"
    ).fetchall()
    remaining_outbound = c.execute(
        "SELECT status, COUNT(*) AS n FROM outbound_messages "
        "WHERE campaign_id=5 AND status IN ('pending','retry','sending') "
        "GROUP BY status"
    ).fetchall()
    print(f"remaining_scheduled={[dict(r) for r in remaining_scheduled]}")
    print(f"remaining_outbound={[dict(r) for r in remaining_outbound]}")
    if remaining_scheduled or remaining_outbound:
        raise RuntimeError("Campaign 5 still has sendable rows; rolling back.")

print("COMMITTED")
