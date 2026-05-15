"""
Calls endpoints — list calls, queue leads for calling, manual dispatch, Vapi webhook.
"""

import logging
from typing import List

import db
from api.deps import get_user_id
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()
log = logging.getLogger("calls")


class QueueCallsBody(BaseModel):
    lead_ids: List[int]


@router.get("")
def list_calls(limit: int = 100, offset: int = 0, user_id: str = Depends(get_user_id)):
    return {"items": db.list_calls(limit=limit, offset=offset)}


@router.get("/summary")
def calls_summary(user_id: str = Depends(get_user_id)):
    return db.calls_summary()


@router.post("/queue")
def queue_calls(body: QueueCallsBody, user_id: str = Depends(get_user_id)):
    """Queue leads (must have phone numbers) for outbound calling."""
    queued, skipped = [], []
    for lid in body.lead_ids:
        lead = db.get_lead(lid)
        if not lead or not lead.get("phone"):
            skipped.append(lid)
            continue
        call_id = db.queue_call(lid)
        queued.append(call_id)
    return {"queued": len(queued), "skipped": len(skipped), "call_ids": queued}


@router.post("/dispatch/{call_id}")
def dispatch_now(call_id: int, user_id: str = Depends(get_user_id)):
    """Immediately dispatch a single queued call via Vapi."""
    from voice_caller import dispatch_call
    calls = db.list_calls(limit=1, offset=0)
    # find by id
    with db.get_conn() as c:
        row = c.execute(
            "SELECT c.*, l.business_name, l.phone, l.city, l.country, l.website "
            "FROM calls c JOIN leads l ON l.id=c.lead_id WHERE c.id=%s",
            (call_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Call not found")
    row = dict(row)
    if row["status"] != "queued":
        raise HTTPException(status_code=400, detail=f"Call is already {row['status']}")
    try:
        vapi = dispatch_call(row)
        db.update_call(call_id, status="initiated",
                       vapi_call_id=vapi.get("id"), initiated_at=db.now())
        return {"ok": True, "vapi_call_id": vapi.get("id")}
    except Exception as e:
        db.update_call(call_id, status="failed", notes=str(e)[:200])
        raise HTTPException(status_code=502, detail=str(e))


# ── Vapi webhook (no auth — called by Vapi servers) ──────────
@router.post("/webhook")
async def vapi_webhook(request: Request):
    """Receive Vapi call lifecycle events."""
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid json"}
    try:
        from voice_caller import process_webhook
        result = process_webhook(payload)
        return {"ok": True, **result}
    except Exception as e:
        log.error(f"webhook error: {e}", exc_info=True)
        return {"ok": False, "error": str(e)[:200]}
