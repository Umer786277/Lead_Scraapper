"""
LLM wrapper for the negotiation agent.

Loads GOOGLE_AI_STUDIO_API_KEY + GOOGLE_AI_STUDIO_MODEL from .env (or the
environment). Talks to Google AI Studio (Gemini) through the REST
generateContent endpoint — no separate SDK needed (requests is already a
dependency). Returns structured JSON — never free-form text — so the UI can
show detected price, suggested action, etc.

Install:
    pip install requests python-dotenv
"""

import json
import os

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, ""))
    except (TypeError, ValueError):
        return default


DEFAULT_MODEL = os.getenv("GOOGLE_AI_STUDIO_MODEL", "gemini-3.6-flash") or "gemini-3.6-flash"
DEFAULT_TEMPERATURE = _env_float("GOOGLE_AI_STUDIO_TEMPERATURE", 0.7)
DEFAULT_MAX_TOKENS = _env_int("GOOGLE_AI_STUDIO_MAX_TOKENS", 150)

# Per-task generation profiles. `max_tokens` here is the *floor* for that
# task — the effective budget is max(GOOGLE_AI_STUDIO_MAX_TOKENS, floor), so
# the env var (default 150) acts as a global preference that can only raise
# output length, never truncate the JSON long tasks need (drafting, city
# lists). Temperatures are fixed per task.
# Thinking models (gemini-3.6-flash) burn output tokens on hidden "thoughts"
# before the visible answer, so keep floors high enough that the real response
# survives: 220-token budgets were being truncated mid-JSON (finishReason=MAX_TOKENS).
_TASK_PROFILES = {
    "draft":    {"temperature": 0.4, "max_tokens": 2048},
    "opener":   {"temperature": 0.6, "max_tokens": 1024},
    "classify": {"temperature": 0.0, "max_tokens": 1024},
    "cities":   {"temperature": 0.0, "max_tokens": 600},
    "note":     {"temperature": 0.4, "max_tokens": 1024},
}


def _budget(task: str) -> tuple[float, int]:
    """Effective (temperature, max_tokens) for a task."""
    profile = _TASK_PROFILES[task]
    return profile["temperature"], max(DEFAULT_MAX_TOKENS, profile["max_tokens"])


_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def _generate(
    model: str,
    contents: list,
    system_prompt: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
    tools: list | None = None,
    timeout: int = 30,
) -> dict:
    """One Gemini generateContent call. Returns the parsed response body.

    Raises RuntimeError (missing key), requests.HTTPError or ValueError on
    failure — callers decide how to fall back.
    """
    api_key = os.getenv("GOOGLE_AI_STUDIO_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GOOGLE_AI_STUDIO_API_KEY not set — add it to .env (or export it) and restart."
        )

    generation: dict = {
        "temperature": DEFAULT_TEMPERATURE if temperature is None else temperature,
        "maxOutputTokens": DEFAULT_MAX_TOKENS if max_tokens is None else max_tokens,
    }
    if json_mode:
        generation["responseMimeType"] = "application/json"

    body: dict = {"contents": contents, "generationConfig": generation}
    if system_prompt:
        body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
    if tools:
        body["tools"] = tools

    resp = requests.post(
        f"{_API_BASE}/models/{model}:generateContent",
        params={"key": api_key},
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def _parts(data: dict) -> list:
    """Return the list of content parts from a generateContent response."""
    candidates = data.get("candidates") or []
    if not candidates:
        return []
    return (candidates[0].get("content") or {}).get("parts") or []


def _text(data: dict) -> str:
    """Concatenated text of the first candidate's parts."""
    return "".join(p.get("text", "") for p in _parts(data))


NEGOTIATION_SYSTEM = """\
You are a professional procurement negotiator acting on behalf of {our_company}.
You negotiate vendor pricing entirely over email. Your goal is to secure the best
possible terms while keeping the relationship professional and long-term friendly.

HARD RULES
- Never be aggressive, manipulative, or dishonest.
- Never reveal the floor price or internal goals to the vendor.
- Cite concrete reasons when pushing back: budget, competing quotes, volume, timeline, commitment length.
- When you counter-offer, anchor with a specific number — never vague "can you do better".
- If the vendor's offer is at or below the floor, ACCEPT and move to close the deal.
- If the vendor will not move meaningfully below their original number after TWO counter-offers, either politely walk away or defer the decision.
- Match the vendor's tone: formal if they are formal, casual if they are casual.
- Keep replies SHORT — 3 to 6 sentences max, email tone, no bullet lists unless the vendor used one.
- If the vendor hasn't given a clear price yet, ASK for one before negotiating.

OUTPUT FORMAT — STRICT JSON, no prose outside the JSON:
{
  "draft_subject":    "reply subject (usually 'Re: ...')",
  "draft_body":       "the full email body you want to send",
  "detected_price":   <number or null — the latest price the vendor quoted>,
  "detected_currency":"<ISO 4217 code or null>",
  "reasoning":        "1-2 sentences of your internal analysis (not sent to vendor)",
  "suggested_action": "counter_offer" | "accept" | "walk_away" | "clarify"
}
"""


def generate_negotiation_draft(
    thread_history,
    price_floor=None,
    currency="USD",
    goal=None,
    our_company="our team",
    model=None,
):
    """
    Generate a draft reply to a vendor thread.

    Args:
        thread_history: list of {"direction": "inbound"|"outbound", "subject": str, "body": str}
                        Oldest first.
        price_floor: the minimum price you'd accept (int/float, optional).
        currency: ISO currency code.
        goal: free-text negotiation objective, e.g., "Below $400 for annual license".
        our_company: what name to use when signing.
        model: Google AI Studio model id. Defaults to GOOGLE_AI_STUDIO_MODEL env.

    Returns:
        dict with keys: draft_subject, draft_body, detected_price, detected_currency,
                        reasoning, suggested_action, model, raw.
    """
    model = model or DEFAULT_MODEL

    lines = [
        f"Currency: {currency}",
        f"Our price floor (INTERNAL — never reveal to vendor): "
        f"{price_floor if price_floor is not None else 'not set'}",
        f"Negotiation goal: {goal or 'secure the lowest sustainable price.'}",
        "",
        "=== CONVERSATION HISTORY (oldest first) ===",
    ]
    for msg in thread_history:
        label = "US" if msg["direction"] == "outbound" else "VENDOR"
        lines.append(f"\n--- {label} ---")
        if msg.get("subject"):
            lines.append(f"Subject: {msg['subject']}")
        lines.append((msg.get("body") or "").strip())

    lines.append("\n=== TASK ===")
    lines.append("Draft our next reply. Output the strict JSON schema only.")

    user_prompt = "\n".join(lines)
    system_prompt = NEGOTIATION_SYSTEM.format(our_company=our_company)

    temp, toks = _budget("draft")
    resp = _generate(
        model,
        contents=[{"role": "user", "parts": [{"text": user_prompt}]}],
        system_prompt=system_prompt,
        temperature=temp,
        max_tokens=toks,
        json_mode=True,
    )
    raw = _text(resp) or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {
            "draft_subject":     "",
            "draft_body":        raw,
            "detected_price":    None,
            "detected_currency": None,
            "reasoning":         "(model returned non-JSON — fell back to raw text)",
            "suggested_action":  "clarify",
        }

    data.setdefault("detected_price", None)
    data.setdefault("detected_currency", None)
    data.setdefault("reasoning", "")
    data.setdefault("suggested_action", "clarify")
    data["model"] = model
    data["raw"] = raw
    return data


# ── Cold-email opener generator ───────────────────────────────
OPENER_SYSTEM = (
    "You generate cold-email personalization data for a single business. "
    "Output STRICT JSON only — no prose outside the JSON.\n\n"
    "Schema:\n"
    '{\n'
    '  "opener": "ONE warm observational sentence (max 20 words) referencing '
    'something specific about the business — a service, niche, location, recent '
    'news, or detail from the website snippet.",\n'
    '  "contact_name": "First name of a real person clearly named as the owner '
    '/ founder / contact on the website snippet, e.g. \'Sarah\' from \'Owner: '
    'Sarah Smith\' or \'John\' from \'Dr. John Patel\'. Set to null if no real '
    'human name appears in the snippet — DO NOT invent one and DO NOT use the '
    'business name."\n'
    '}\n\n'
    "Use the web_search tool ONCE if the snippet is generic/empty and you need "
    "a specific signal. Skip the tool if the snippet already has something "
    "concrete. Never make a second search.\n\n"
    "OPENER RULES: no greeting (no 'Hi', 'Hello'). No pitching. No exclamation "
    "marks or hype. No invented facts — if nothing concrete, write a neutral "
    "location/niche observation. No surrounding quotes."
)

WEB_SEARCH_TOOL = {
    "functionDeclarations": [
        {
            "name": "web_search",
            "description": (
                "Search the web for a recent, specific, public signal about this "
                "business — e.g. press mentions, new locations, awards, hiring "
                "posts, services they highlight. Use a focused query like "
                "'<business name> <city>' or '<business name> news'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Concise web search query, ideally with the business name + a context word.",
                    },
                },
                "required": ["query"],
            },
        },
    ],
}


def _web_search(query: str) -> str:
    """Run a Tavily search. Returns concatenated snippets or "" on any failure.

    Free tier (1000/mo) at tavily.com — set TAVILY_API_KEY to enable.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key or not query:
        return ""
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key":      api_key,
                "query":        query,
                "max_results":  3,
                "include_answer": False,
                "search_depth": "basic",
            },
            timeout=10,
        )
        if not r.ok:
            return ""
        results = (r.json() or {}).get("results", [])
        return "\n".join(
            f"- {(x.get('title') or '').strip()}: {(x.get('content') or '').strip()[:240]}"
            for x in results[:3]
        ) or ""
    except Exception:
        return ""


def generate_opener(lead: dict, model: str | None = None) -> dict:
    """Generate personalized opener + contact name for a lead.

    Returns {"opener": str, "contact_name": str | None}. Both fields default
    to "" / None on any failure (no API key, network error, parse error) so
    the caller can persist whatever was found and fall back gracefully.

    Single LLM call does double duty — same cost as opener-only.
    """
    empty = {"opener": "", "contact_name": None}

    if not os.getenv("GOOGLE_AI_STUDIO_API_KEY"):
        return empty

    business = (lead.get("business_name") or "").strip()
    city     = (lead.get("city") or "").strip()
    country  = (lead.get("country") or "").strip()
    snippet  = (lead.get("homepage_snippet") or "").strip()[:1200]

    if not business and not snippet:
        return empty

    user = (
        f"Business: {business or 'unknown'}\n"
        f"Location: {city}{', ' + country if country else ''}\n"
        f"Website snippet: {snippet or '(none)'}\n\n"
        f"Return the JSON object."
    )

    model = model or DEFAULT_MODEL
    use_tools = bool(os.getenv("TAVILY_API_KEY"))
    tools = [WEB_SEARCH_TOOL] if use_tools else None
    contents = [{"role": "user", "parts": [{"text": user}]}]

    try:
        # Allow up to 2 turns: one tool call, then the final JSON.
        temp, toks = _budget("opener")
        resp = _generate(
            model, contents,
            system_prompt=OPENER_SYSTEM,
            temperature=temp,
            max_tokens=toks,
            tools=tools,
        )
        parts = _parts(resp)

        if use_tools:
            for p in parts:
                fc = p.get("functionCall")
                if fc and fc.get("name") == "web_search":
                    args = fc.get("args") or {}
                    result = _web_search(args.get("query", ""))
                    contents.append({
                        "role":  "model",
                        "parts": [{"functionCall": fc}],
                    })
                    contents.append({
                        "role":  "user",
                        "parts": [{
                            "functionResponse": {
                                "name":     "web_search",
                                "response": {"result": result or "(no useful results)"},
                            },
                        }],
                    })
                    # Final turn returns JSON, no more tools
                    resp = _generate(
                        model, contents,
                        system_prompt=OPENER_SYSTEM,
                        temperature=temp,
                        max_tokens=toks,
                        json_mode=True,
                    )
                    parts = _parts(resp)
                    break

        raw = "".join(p.get("text", "") for p in parts) or "{}"
        data = json.loads(raw)
    except Exception:
        return empty

    opener = (data.get("opener") or "").strip()
    if len(opener) >= 2 and opener[0] in "\"'" and opener[-1] in "\"'":
        opener = opener[1:-1].strip()

    name = data.get("contact_name")
    if isinstance(name, str):
        name = name.strip() or None
    else:
        name = None

    return {"opener": opener, "contact_name": name}


# ── Improvement note (per-lead prep for outreach) ──────────────
IMPROVEMENT_NOTE_SYSTEM = """\
You evaluate a single website to spot a concrete call-handling gap that an AI
receptionist named CallMissa could fix. CallMissa answers calls 24/7, books and
reschedules appointments, and triages after-hours emergencies so no call goes to
voicemail.

Look specifically for signals like: no visible online booking, no mention of
after-hours/emergency call handling, phone-only contact with no click-to-call, or
a small team likely stretched thin at the front desk.

Output STRICT JSON only — no prose outside the JSON:
{
  "note": "1-2 sentences, specific and factual about this site, OR the exact string NONE if nothing suggests a call-handling gap",
  "lead_line": "ONE sentence written to the clinic owner themselves — a factual observation or question drawn ONLY from this site, phrased naturally for the opening line of a short cold email, OR the exact string NONE if nothing suggests a gap"
}

HARD RULES
- Do not invent details not present on the page.
- No generic marketing advice (SEO, social media, branding, website design).
- lead_line must never mention CallMissa, AI, receptionists, or any product.
- lead_line must sound like a real person wrote it, not an ad. Second person ("you/your").
- Keep both usable as prep for both the human sales team and outreach copy.
"""


def _clean_note_value(value, maxlen=500) -> str:
    """Normalize a note/lead_line value: strip, clamp, drop 'NONE'."""
    if not value:
        return ""
    value = str(value).strip()
    if value.upper() == "NONE":
        return ""
    return value[:maxlen]


def generate_improvement_note(domain: str, snippet: str, model: str | None = None) -> dict | None:
    """Return {"note": str, "lead_line": str} for a lead, or None on failure.

    One LLM call produces both an internal prep note (leads.improvement_note)
    and a client-facing opening line (leads.lead_line) that is safe to drop
    into the first line of a cold email. Either value is "" when the model
    judges there's no call-handling gap or the call fails.
    """
    if not os.getenv("GOOGLE_AI_STUDIO_API_KEY") or not snippet:
        return None
    user = (
        f"Business website: {domain or 'unknown'}\n"
        f"Website text (homepage / about / services): {snippet[:1200]}\n\n"
        f"Return the JSON object."
    )
    try:
        temp, toks = _budget("note")
        resp = _generate(
            model or DEFAULT_MODEL,
            contents=[{"role": "user", "parts": [{"text": user}]}],
            system_prompt=IMPROVEMENT_NOTE_SYSTEM,
            temperature=temp,
            max_tokens=toks,
            json_mode=True,
        )
        data = json.loads(_text(resp) or "{}")
        return {
            "note":      _clean_note_value(data.get("note")),
            "lead_line": _clean_note_value(data.get("lead_line")),
        }
    except Exception:
        return None


# ── Reply triage (inbound lead replies) ────────────────────────
REPLY_TRIAGE_SYSTEM = """\
You triage an inbound reply to a cold outreach email so an automated \
follow-up sequence knows how to react.

Output STRICT JSON only — no prose outside the JSON:
{
  "intent": "interested" | "needs_info" | "bad_timing" | "not_interested" | "unsubscribe" | "other",
  "follow_up_days": <integer or null>,
  "reasoning": "1 short sentence explaining the classification"
}

GUIDANCE
- "interested": wants to talk, asked to book a call/demo, or is clearly positive. follow_up_days: null (a human takes over).
- "needs_info": asked a question that needs answering before they'll decide. follow_up_days: null (a human should reply directly).
- "bad_timing": positive-but-not-now (e.g. "traveling", "check back next quarter", "swamped this week"). Set follow_up_days to the number of days implied — "next week" -> 7, "next month" -> 30, "next quarter" -> 90, vague/unspecified -> 14.
- "not_interested": a clear no, not hostile. follow_up_days: null.
- "unsubscribe": explicitly asked to stop / remove / unsubscribe. follow_up_days: null.
- "other": doesn't fit any of the above (auto-reply, out-of-office, spam, unrelated). follow_up_days: null.
"""

_REPLY_INTENTS = {
    "interested", "needs_info", "bad_timing",
    "not_interested", "unsubscribe", "other",
}


def classify_reply(lead: dict, thread_history: list, model: str | None = None) -> dict:
    """Classify an inbound reply's intent so the outreach sequence can react
    (stop for a human, pause + resume, or cancel) instead of always
    cancelling on any reply.

    thread_history: list of {"direction": "inbound"|"outbound", "subject": str, "body": str},
                     oldest first.

    Returns {"intent": str, "follow_up_days": int | None, "reasoning": str}.
    Defaults to intent="other" on any failure (no API key, network error,
    parse error) so the caller can fall back to the previous safe behavior.
    """
    fallback = {"intent": "other", "follow_up_days": None, "reasoning": ""}

    if not os.getenv("GOOGLE_AI_STUDIO_API_KEY"):
        return fallback

    business = (lead.get("business_name") or "").strip()
    lines = [f"Business: {business or 'unknown'}", "", "=== CONVERSATION HISTORY (oldest first) ==="]
    for msg in thread_history:
        label = "US" if msg["direction"] == "outbound" else "PROSPECT"
        lines.append(f"\n--- {label} ---")
        if msg.get("subject"):
            lines.append(f"Subject: {msg['subject']}")
        lines.append((msg.get("body") or "").strip())

    try:
        temp, toks = _budget("classify")
        resp = _generate(
            model or DEFAULT_MODEL,
            contents=[{"role": "user", "parts": [{"text": "\n".join(lines)}]}],
            system_prompt=REPLY_TRIAGE_SYSTEM,
            temperature=temp,
            max_tokens=toks,
            json_mode=True,
        )
        data = json.loads(_text(resp) or "{}")
    except Exception:
        return fallback

    intent = data.get("intent")
    if intent not in _REPLY_INTENTS:
        intent = "other"

    follow_up_days = data.get("follow_up_days")
    try:
        follow_up_days = int(follow_up_days) if follow_up_days is not None else None
    except (TypeError, ValueError):
        follow_up_days = None

    return {
        "intent": intent,
        "follow_up_days": follow_up_days,
        "reasoning": (data.get("reasoning") or "").strip(),
    }


# ── Country → cities lookup (LLM fallback for hybrid cities.py) ───────
CITIES_FETCH_SYSTEM = (
    "You are a structured-data assistant. Output JSON only — no prose, "
    "no commentary. Stick to verifiable population rankings."
)


def fetch_cities(country: str, count: int = 25) -> list[str]:
    """Ask the model for the largest cities in a country.

    Used by cities.py only when the country is missing from the static map
    AND not already cached. Returns [] on any failure — caller decides what
    to do (cities.py treats [] as "no cities, skip seeding queries").
    """
    if not os.getenv("GOOGLE_AI_STUDIO_API_KEY") or not country:
        return []

    user = (
        f"List the {count} largest cities in {country.strip()} ranked by "
        f"population (largest first). Return ONLY this JSON shape:\n"
        f'{{"cities": ["City A", "City B", ...]}}\n'
        f"Cities must be real, currently inhabited, and located in that country. "
        f"No country names, no regions, no states — only city names."
    )

    try:
        temp, toks = _budget("cities")
        resp = _generate(
            DEFAULT_MODEL,
            contents=[{"role": "user", "parts": [{"text": user}]}],
            system_prompt=CITIES_FETCH_SYSTEM,
            temperature=temp,
            max_tokens=toks,
            json_mode=True,
        )
        data = json.loads(_text(resp) or "{}")
        raw = data.get("cities", [])
        return [c.strip() for c in raw if isinstance(c, str) and c.strip()][:count]
    except Exception:
        return []