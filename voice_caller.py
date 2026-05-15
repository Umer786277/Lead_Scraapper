"""
Vapi.ai outbound calling integration.

Required env vars:
    VAPI_API_KEY          — from https://dashboard.vapi.ai
    VAPI_PHONE_NUMBER_ID  — phone number to call from (Vapi dashboard)

Optional:
    VAPI_ASSISTANT_ID     — use a saved assistant; if unset, builds one inline
"""

import logging
import os

import requests

log = logging.getLogger("voice_caller")

VAPI_API_KEY      = lambda: os.getenv("VAPI_API_KEY", "")
VAPI_PHONE_ID     = lambda: os.getenv("VAPI_PHONE_NUMBER_ID", "")
VAPI_ASSISTANT_ID = lambda: os.getenv("VAPI_ASSISTANT_ID", "")
VAPI_BASE         = "https://api.vapi.ai"


def _headers():
    return {"Authorization": f"Bearer {VAPI_API_KEY()}", "Content-Type": "application/json"}


def dispatch_call(lead: dict) -> dict:
    """
    Initiate an outbound Vapi call for the given lead dict.
    Returns the Vapi call object on success, raises on failure.
    """
    if not VAPI_API_KEY():
        raise RuntimeError("VAPI_API_KEY is not set")
    if not VAPI_PHONE_ID():
        raise RuntimeError("VAPI_PHONE_NUMBER_ID is not set")

    phone = (lead.get("phone") or "").strip()
    if not phone:
        raise ValueError(f"Lead {lead.get('id')} has no phone number")

    biz = lead.get("business_name") or "your business"

    payload: dict = {
        "phoneNumberId": VAPI_PHONE_ID(),
        "customer": {"number": phone, "name": biz},
    }

    if VAPI_ASSISTANT_ID():
        payload["assistantId"] = VAPI_ASSISTANT_ID()
        payload["assistantOverrides"] = {
            "variableValues": {
                "business_name": biz,
                "city":    lead.get("city") or "",
                "country": lead.get("country") or "",
                "website": lead.get("website") or "",
            }
        }
    else:
        payload["assistant"] = _build_assistant(lead)

    resp = requests.post(f"{VAPI_BASE}/call", json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _build_assistant(lead: dict) -> dict:
    biz     = lead.get("business_name") or "your business"
    city    = lead.get("city") or ""
    country = lead.get("country") or ""
    loc     = f" in {city}, {country}" if city else ""

    system_prompt = f"""You are Alex, a friendly business development representative calling {biz}{loc}.

Your goals:
1. Introduce yourself: "Hi, I'm Alex calling from GetColdWire — we help local businesses get more clients online."
2. Ask if they are the owner or manager.
3. Ask if they are currently looking to grow their client base.
4. If interested: ask about their biggest challenge getting new clients, mention you can send them a free growth report by email.
5. If not interested: thank them politely and end the call.

Keep responses short and conversational. Never read from a script verbatim."""

    return {
        "name": "LeadFlow Qualifier",
        "model": {
            "provider": "openai",
            "model":    "gpt-4o-mini",
            "messages": [{"role": "system", "content": system_prompt}],
        },
        "voice": {
            "provider": "11labs",
            "voiceId":  "21m00Tcm4TlvDq8ikWAM",  # Rachel — natural, professional
        },
        "firstMessage": f"Hi, this is Alex calling from GetColdWire. Am I speaking with someone from {biz}?",
        "endCallMessage": "Thank you for your time. Have a wonderful day!",
        "endCallFunctionEnabled": True,
        "recordingEnabled": True,
        "transcriber": {
            "provider": "deepgram",
            "model":    "nova-2",
            "language": "en",
        },
        "analysisPlan": {
            "summaryPrompt": (
                "Summarize this sales qualification call in 2-3 sentences. "
                "Note whether the prospect was interested and any key details mentioned."
            ),
            "structuredDataPrompt": (
                "Extract: interested (true/false), "
                "is_decision_maker (true/false), "
                "callback_requested (true/false), "
                "notes (brief string with key points)."
            ),
            "structuredDataSchema": {
                "type": "object",
                "properties": {
                    "interested":          {"type": "boolean"},
                    "is_decision_maker":   {"type": "boolean"},
                    "callback_requested":  {"type": "boolean"},
                    "notes":               {"type": "string"},
                },
            },
        },
    }


def process_webhook(payload: dict) -> dict:
    """Handle a Vapi webhook event and update the DB accordingly."""
    import db

    msg      = payload.get("message", {})
    msg_type = msg.get("type") or payload.get("type", "")
    call_obj = msg.get("call") or payload.get("call") or {}
    vapi_id  = call_obj.get("id", "")

    if not vapi_id:
        return {"action": "ignored", "reason": "no call id"}

    if msg_type == "call-started":
        db.update_call_by_vapi_id(vapi_id, status="in-progress", initiated_at=db.now())
        log.info(f"call {vapi_id} started")
        return {"action": "updated", "status": "in-progress"}

    if msg_type == "end-of-call-report":
        ended_reason  = msg.get("endedReason", "")
        duration_sec  = int(msg.get("durationSeconds") or 0)
        transcript    = msg.get("transcript", "")
        summary       = msg.get("summary", "")
        recording_url = msg.get("recordingUrl", "")

        analysis   = msg.get("analysis", {})
        structured = analysis.get("structuredData") or {}
        interested = structured.get("interested")
        qualified  = "yes" if interested is True else ("no" if interested is False else None)
        notes      = structured.get("notes", "")

        db.update_call_by_vapi_id(
            vapi_id,
            status="completed",
            ended_reason=ended_reason,
            duration_sec=duration_sec,
            transcript=transcript[:10000] if transcript else None,
            summary=summary[:1000]        if summary    else None,
            recording_url=recording_url   or None,
            qualified=qualified,
            notes=notes[:500]             if notes      else None,
            ended_at=db.now(),
        )

        if qualified == "yes":
            try:
                lead_id = db.get_call_lead_id(vapi_id)
                if lead_id:
                    db.update_lead_status(lead_id, "contacted",
                                          note=f"Voice call qualified: {summary[:200]}")
            except Exception as e:
                log.warning(f"could not update lead status: {e}")

        log.info(f"call {vapi_id} ended: {ended_reason}, {duration_sec}s, qualified={qualified}")
        return {"action": "updated", "status": "completed", "qualified": qualified}

    if msg_type in ("call-failed", "hang"):
        db.update_call_by_vapi_id(vapi_id, status="failed",
                                  ended_reason=msg_type, ended_at=db.now())
        return {"action": "updated", "status": "failed"}

    return {"action": "ignored", "type": msg_type}
