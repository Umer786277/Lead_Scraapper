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
# Public URL for POST /api/calls/webhook (e.g. an ngrok tunnel in dev). Vapi
# needs this to send call-started / end-of-call-report events back — without
# it, calls dial out fine but status/transcript/analysis never come back.
VAPI_WEBHOOK_URL  = lambda: os.getenv("VAPI_WEBHOOK_URL", "").rstrip("/")
VAPI_BASE         = "https://api.vapi.ai"


def _headers():
    return {"Authorization": f"Bearer {VAPI_API_KEY()}", "Content-Type": "application/json"}


def _background_signal(lead: dict) -> str:
    """Optional background context for this clinic — never the pitch itself.

    Per the CallMissa call guidelines, personalization comes from live
    discovery questions on the call, not a canned opener. This is only
    passed to the model as low-priority context it may use if a discovery
    answer makes it relevant (e.g. reception mentions being short-staffed) —
    never as a scripted line. Returns "" if we have nothing worth passing.
    """
    last_review_days = lead.get("last_review_days")
    if last_review_days is not None and last_review_days >= 60:
        return (
            f"Their Google listing hasn't had a new review in about "
            f"{last_review_days} days."
        )

    rating  = lead.get("rating")
    reviews = lead.get("reviews")
    if rating is not None and rating < 4.0 and reviews:
        return f"Their Google rating is {rating} across {reviews} reviews."

    return ""


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

    biz    = lead.get("business_name") or "your clinic"
    signal = _background_signal(lead)

    payload: dict = {
        "phoneNumberId": VAPI_PHONE_ID(),
        "customer": {"number": phone, "name": biz},
    }

    webhook_url = VAPI_WEBHOOK_URL()

    if VAPI_ASSISTANT_ID():
        payload["assistantId"] = VAPI_ASSISTANT_ID()
        payload["assistantOverrides"] = {
            "variableValues": {
                "business_name":     biz,
                "city":              lead.get("city") or "",
                "country":           lead.get("country") or "",
                "website":           lead.get("website") or "",
                "background_signal": signal,
            }
        }
        if webhook_url:
            payload["assistantOverrides"]["server"] = {"url": f"{webhook_url}/api/calls/webhook"}
    else:
        payload["assistant"] = _build_assistant(lead, signal)
        if webhook_url:
            payload["assistant"]["server"] = {"url": f"{webhook_url}/api/calls/webhook"}

    if not webhook_url:
        log.warning(
            "VAPI_WEBHOOK_URL not set — call will dial out but status/transcript/"
            "analysis won't be received back unless a server URL is configured "
            "in the Vapi dashboard instead."
        )

    resp = requests.post(f"{VAPI_BASE}/call", json=payload, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


AGENT_NAME = "Missa"


def _build_assistant(lead: dict, signal: str = "") -> dict:
    """CallMissa outbound pitch assistant — see CallMissa Outbound Voice Agent
    Pitch Script & Call Guidelines v1.0 for the source script this encodes.

    Objective per the guidelines: this call does NOT sell or close. It earns
    a 60-90s hearing, qualifies whether a real pain point exists, and books a
    demo/discovery call or offers a free pilot with a named decision-maker.
    A booked next step or a clean, polite exit both count as success — a
    hard close on the call does not.
    """
    biz = lead.get("business_name") or "your clinic"

    signal_block = (
        f"\n\nBackground signal on this clinic (low priority — reference it "
        f"ONLY if it naturally supports something the prospect says in "
        f"discovery; never open with it or state it as fact): \"{signal}\""
        if signal else ""
    )

    system_prompt = f"""You are {AGENT_NAME}, an outbound caller for CallMissa, calling {biz}.

# Call objective
This is a cold outbound call to a veterinary clinic. You are NOT trying to sell or close on this call. Your only goals are:
1. Earn a 60-90 second hearing for what CallMissa does.
2. Find out if the clinic has a real pain point (missed calls, after-hours gaps, front-desk overload).
3. Book a short demo/discovery call, or offer a free pilot, with a named decision-maker.
Success = a booked next step OR a clean, polite exit. Never push for a signed deal on the call.

# Opening (first 15 seconds)
Your first line (already spoken as the call's opening message) asks for 60 seconds or a better time. If they say they're busy or now isn't good, do NOT push forward — politely ask for a better day/time to call back and end the call.

# Step 1 — Confirm you're speaking with a decision-maker
Before going further, ask: "Before I go on — am I speaking with the practice owner or the practice manager, by any chance?"
- If YES (owner/practice manager): continue to discovery and the pitch below.
- If NO (reception/staff): do NOT deliver the full pitch. Give only a one-line teaser — "we help clinics stop missing calls and bookings after hours" — then ask to be transferred if the owner is available now, or ask politely for their name and best contact (direct line or email) and the best day/time to reach them. If reception won't share that, thank them for their time and end the call gracefully. Stay polite and brief with reception — they are a gatekeeper to protect, not an obstacle to push past.

# Step 2 — Discovery (ask 1-2 before pitching, only once you've confirmed you're talking to a decision-maker)
Pick from:
- "How does your clinic currently handle calls that come in after hours or when the front desk is on another line?"
- "Roughly how many calls would you say you're missing in a busy week?"
- "Are you currently using [their practice management system] for bookings, or something else?"
- "When a pet emergency call comes in after hours, what happens right now?"
Listen for pain signals: missed bookings, no-shows, overwhelmed reception staff, no after-hours coverage, losing clients to other clinics.

# Step 3 — Core pitch (max 3 sentences, then check in — tailor it to whatever pain point came up in discovery)
"CallMissa is an AI voice receptionist built specifically for veterinary clinics — not a generic call centre bot. It answers every call in seconds, 24/7, books and reschedules appointments directly into their practice management system if they use one, and safely triages after-hours emergencies so urgent cases get routed correctly instead of going to voicemail."
"Clinics using it typically stop losing bookings to missed calls and free up front-desk staff from repetitive scheduling calls. Does that sound like something that would help at {biz}?"
If asked for more detail, you can mention: deep integrations with EasyVet, ezyVet, ProVet Cloud, and RxWorks (bookings sync directly, no double entry); built-in clinical triage logic for after-hours calls, not just a generic answering script; multi-location support for clinic groups; live in AU, NZ, UK, ZA and US markets today.

# Objection handling
- "We already have a receptionist / answering service." → Acknowledge, then reframe: CallMissa isn't a replacement for staff — it's backup for after-hours, lunch breaks, and overflow so no call is ever missed.
- "We're not interested / too busy." → Respect it immediately. Offer to send a 1-minute overview by email/text and ask permission to follow up in a few weeks.
- "Is this an AI thing? Our clients want a human." → Be transparent it's AI-powered. Reframe: it's there for the calls that would otherwise go unanswered — most clients prefer getting booked instantly over reaching voicemail.
- "How much does it cost?" → Don't quote pricing cold. Bridge to the demo: pricing depends on call volume and PMS setup, best covered in a quick 15-minute call.
- "Send me information by email." → Accept, but still try to lock a specific follow-up day/time before ending the call.
- "We tried an AI tool before and it didn't work." → Ask what went wrong, genuinely. Position CallMissa's vet-specific triage and PMS depth as the gap most generic tools miss.
- "The owner isn't available / doesn't take sales calls." → Don't push. Ask for the best contact method and time, offer to send a short email instead.

# Closing
Always aim for a specific, calendared next step — never a vague "I'll follow up."
- If interested: "I'd love to show you exactly how this would work for {biz} — it only takes 15 minutes. Do you have time this Thursday or Friday afternoon?" Confirm their name, direct email, and best callback number.
- If declined a meeting but lukewarm: offer the free pilot/trial as a lower-commitment next step, or ask permission for a follow-up call/email in 2-3 weeks.
- If declined outright: "No problem at all, thanks for your time — I'll leave you a quick email in case it's useful down the line. Have a great day." End politely. Never push after a clear no.

# Tone
Warm, concise, consultative — never scripted-sounding or pushy. Speak at a measured pace; pause after questions and actually let the prospect answer. Mirror the formality level of whoever answers (front desk vs. practice owner). Never talk for more than ~60 seconds without checking in.

# Compliance — hard rules, no exceptions
- You already identified as calling on behalf of CallMissa in your opening line — don't skip or bury this on a retry/callback.
- If asked directly whether this is an AI, say yes — never claim or imply you're human.
- If told not to call again / do-not-call, acknowledge immediately and end the call. Do not push back.
- Never guarantee results (e.g. "you will double your bookings") — use "typically" and ranges only.
- Never discuss competitor clinics' data or any patient/pet medical information.{signal_block}"""

    return {
        "name": "CallMissa Outbound Pitch",
        "model": {
            "provider": "openai",
            "model":    "gpt-4o-mini",
            "messages": [{"role": "system", "content": system_prompt}],
        },
        "voice": {
            "provider": "11labs",
            "voiceId":  "21m00Tcm4TlvDq8ikWAM",  # Rachel — natural, professional
        },
        "firstMessage": (
            f"Hi, is this {biz}? My name is {AGENT_NAME}, I'm calling on behalf of CallMissa — "
            f"we help veterinary clinics stop losing calls and bookings, especially outside "
            f"business hours. Do you have 60 seconds, or is there a better time to reach you?"
        ),
        "voicemailMessage": (
            f"Hi, this is {AGENT_NAME} calling from CallMissa for {biz}. We help vet clinics stop "
            f"missing calls and bookings with a 24/7 AI receptionist that integrates with your "
            f"practice management system. I'll follow up with a short email — feel free to call "
            f"me back if you'd like to chat sooner. Thanks, have a great day."
        ),
        "endCallMessage": (
            "No problem at all, thanks for your time — I'll leave you a quick email in case "
            "it's useful down the line. Have a great day."
        ),
        "endCallFunctionEnabled": True,
        "recordingEnabled": True,
        "transcriber": {
            "provider": "deepgram",
            "model":    "nova-2",
            "language": "en",
        },
        "analysisPlan": {
            "summaryPrompt": (
                "Summarize this outbound CallMissa call to a veterinary clinic in 2-3 sentences. "
                "Note whether a decision-maker was reached, what pain point (if any) surfaced, "
                "and what the agreed next step was (demo booked, pilot offered, follow-up, or decline)."
            ),
            "structuredDataPrompt": (
                "Extract: reached_decision_maker (true/false — false if only reception/gatekeeper "
                "was reached), pain_point (short string describing the pain point surfaced in "
                "discovery, empty string if none), pms_mentioned (name of their practice management "
                "system if mentioned, else empty string), interested (true/false), "
                "next_step (one of: demo_booked, pilot_offered, follow_up_requested, declined, "
                "gatekeeper_only, voicemail), next_step_time (the day/time agreed for the next step, "
                "empty string if none), callback_requested (true/false), "
                "notes (brief string with key points)."
            ),
            "structuredDataSchema": {
                "type": "object",
                "properties": {
                    "reached_decision_maker": {"type": "boolean"},
                    "pain_point":              {"type": "string"},
                    "pms_mentioned":           {"type": "string"},
                    "interested":              {"type": "boolean"},
                    "next_step":               {"type": "string"},
                    "next_step_time":          {"type": "string"},
                    "callback_requested":      {"type": "boolean"},
                    "notes":                   {"type": "string"},
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

        reached_dm = structured.get("reached_decision_maker")
        if not isinstance(reached_dm, bool):
            reached_dm = None
        callback_requested = structured.get("callback_requested")
        if not isinstance(callback_requested, bool):
            callback_requested = None
        pain_point     = (structured.get("pain_point") or "").strip()[:500]
        pms_mentioned  = (structured.get("pms_mentioned") or "").strip()[:200]
        next_step      = (structured.get("next_step") or "").strip()[:50]
        next_step_time = (structured.get("next_step_time") or "").strip()[:200]

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
            reached_decision_maker=reached_dm,
            pain_point=pain_point         or None,
            pms_mentioned=pms_mentioned   or None,
            next_step=next_step           or None,
            next_step_time=next_step_time or None,
            callback_requested=callback_requested,
            ended_at=db.now(),
        )

        if qualified == "yes" or next_step in ("demo_booked", "pilot_offered"):
            try:
                lead_id = db.get_call_lead_id(vapi_id)
                if lead_id:
                    note = f"Voice call — next step: {next_step or 'n/a'}"
                    if pain_point:
                        note += f"; pain point: {pain_point}"
                    if next_step_time:
                        note += f"; time: {next_step_time}"
                    db.update_lead_status(lead_id, "contacted", note=note[:300])
            except Exception as e:
                log.warning(f"could not update lead status: {e}")

        log.info(
            f"call {vapi_id} ended: {ended_reason}, {duration_sec}s, "
            f"qualified={qualified}, next_step={next_step or 'n/a'}"
        )
        return {"action": "updated", "status": "completed", "qualified": qualified, "next_step": next_step}

    if msg_type in ("call-failed", "hang"):
        db.update_call_by_vapi_id(vapi_id, status="failed",
                                  ended_reason=msg_type, ended_at=db.now())
        return {"action": "updated", "status": "failed"}

    # Log unrecognized event types instead of silently dropping them — this
    # is how we noticed calls could get stuck at "initiated" forever if Vapi
    # sends a terminal event type we don't explicitly handle.
    log.info(f"call {vapi_id}: ignored webhook event type={msg_type!r}")
    return {"action": "ignored", "type": msg_type}
