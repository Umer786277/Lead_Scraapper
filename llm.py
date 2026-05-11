"""
LLM wrapper for the negotiation agent.

Loads OPENAI_API_KEY + OPENAI_MODEL from .env (or the environment).
Returns structured JSON — never free-form text — so the UI can show
detected price, suggested action, etc.

Install:
    pip install openai python-dotenv
"""

import json
import os

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from openai import OpenAI


DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_client = None


def get_client():
    global _client
    if _client is None:
        key = os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY not set — add it to .env (or export it) and restart."
            )
        _client = OpenAI(api_key=key)
    return _client


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
        model: OpenAI model id. Defaults to OPENAI_MODEL env or 'gpt-4o-mini'.

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

    client = get_client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.4,
    )
    raw = resp.choices[0].message.content or "{}"
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
    "type": "function",
    "function": {
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

    if not os.getenv("OPENAI_API_KEY"):
        return empty

    business = (lead.get("business_name") or "").strip()
    city     = (lead.get("city") or "").strip()
    country  = (lead.get("country") or "").strip()
    snippet  = (lead.get("homepage_snippet") or "").strip()[:600]

    if not business and not snippet:
        return empty

    user = (
        f"Business: {business or 'unknown'}\n"
        f"Location: {city}{', ' + country if country else ''}\n"
        f"Website snippet: {snippet or '(none)'}\n\n"
        f"Return the JSON object."
    )

    messages = [
        {"role": "system", "content": OPENER_SYSTEM},
        {"role": "user",   "content": user},
    ]
    use_tools = bool(os.getenv("TAVILY_API_KEY"))

    try:
        client = get_client()
        # Allow up to 2 turns: one tool call, then the final JSON.
        for _ in range(2):
            kwargs = {
                "model":       model or DEFAULT_MODEL,
                "messages":    messages,
                "temperature": 0.6,
                "max_tokens":  220,
            }
            if use_tools:
                kwargs["tools"] = [WEB_SEARCH_TOOL]
                kwargs["tool_choice"] = "auto"
            else:
                # Only enforce JSON mode on the final non-tool turn — tool
                # calls go through the normal text path.
                kwargs["response_format"] = {"type": "json_object"}

            resp = client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message

            if getattr(msg, "tool_calls", None):
                messages.append(msg.model_dump())
                for tc in msg.tool_calls:
                    if tc.function.name == "web_search":
                        try:
                            args = json.loads(tc.function.arguments or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        result = _web_search(args.get("query", ""))
                        messages.append({
                            "role":         "tool",
                            "tool_call_id": tc.id,
                            "content":      result or "(no useful results)",
                        })
                use_tools = False  # final turn returns JSON, no more tools
                continue

            try:
                data = json.loads(msg.content or "{}")
            except json.JSONDecodeError:
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
    except Exception:
        return empty

    return empty


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
    if not os.getenv("OPENAI_API_KEY") or not country:
        return []

    user = (
        f"List the {count} largest cities in {country.strip()} ranked by "
        f"population (largest first). Return ONLY this JSON shape:\n"
        f'{{"cities": ["City A", "City B", ...]}}\n'
        f"Cities must be real, currently inhabited, and located in that country. "
        f"No country names, no regions, no states — only city names."
    )

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": CITIES_FETCH_SYSTEM},
                {"role": "user",   "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=600,
        )
        data = json.loads(resp.choices[0].message.content or "{}")
        raw = data.get("cities", [])
        return [c.strip() for c in raw if isinstance(c, str) and c.strip()][:count]
    except Exception:
        return []
