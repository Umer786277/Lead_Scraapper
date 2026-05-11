"""
Country/city → timezone resolution + business-hours gating.

Used by outreach.process_queue() to defer sends that would land outside
the recipient's local 9am-5pm window. Cold emails sent at 3am local time
sit at the bottom of the inbox by morning — major reply-rate killer.

Zero external deps — uses zoneinfo (stdlib in Python 3.9+).
"""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# ── Country defaults — single timezone covers ~95% of the country ────
# Country aliases are normalized via cities.canonical_country first.
COUNTRY_TZ: dict[str, str] = {
    "UK": "Europe/London",
    "IE": "Europe/Dublin",
    "AU": "Australia/Sydney",      # default — overridden per-city below
    "NZ": "Pacific/Auckland",
    "CA": "America/Toronto",       # default — overridden per-city below
    "US": "America/New_York",      # default — overridden per-city below
}

# ── City overrides for multi-timezone countries ──────────────────────
# Lower-cased city → timezone. Anything not listed falls back to COUNTRY_TZ.
CITY_TZ: dict[str, str] = {
    # US — 6 zones
    "los angeles":   "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "san diego":     "America/Los_Angeles",
    "san jose":      "America/Los_Angeles",
    "seattle":       "America/Los_Angeles",
    "portland":      "America/Los_Angeles",
    "las vegas":     "America/Los_Angeles",
    "phoenix":       "America/Phoenix",
    "denver":        "America/Denver",
    "salt lake city":"America/Denver",
    "albuquerque":   "America/Denver",
    "chicago":       "America/Chicago",
    "houston":       "America/Chicago",
    "dallas":        "America/Chicago",
    "austin":        "America/Chicago",
    "san antonio":   "America/Chicago",
    "fort worth":    "America/Chicago",
    "memphis":       "America/Chicago",
    "milwaukee":     "America/Chicago",
    "nashville":     "America/Chicago",
    "new orleans":   "America/Chicago",
    "minneapolis":   "America/Chicago",
    "kansas city":   "America/Chicago",
    "indianapolis":  "America/Indiana/Indianapolis",
    # CA — 6 zones
    "vancouver":     "America/Vancouver",
    "victoria":      "America/Vancouver",
    "calgary":       "America/Edmonton",
    "edmonton":      "America/Edmonton",
    "saskatoon":     "America/Regina",
    "regina":        "America/Regina",
    "winnipeg":      "America/Winnipeg",
    "halifax":       "America/Halifax",
    "st. john's":    "America/St_Johns",
    # AU — 5 zones
    "perth":         "Australia/Perth",
    "adelaide":      "Australia/Adelaide",
    "darwin":        "Australia/Darwin",
    "brisbane":      "Australia/Brisbane",
    "hobart":        "Australia/Hobart",
}


def tz_for(country: str | None, city: str | None = None) -> str | None:
    """Resolve a lead's timezone from country (and optionally city).

    Returns IANA timezone name (e.g. 'Europe/London') or None if unknown.
    """
    if city:
        hit = CITY_TZ.get(city.strip().lower())
        if hit:
            return hit

    # Reuse the country canonicalization from cities.py so 'United Kingdom',
    # 'GB', 'England' all resolve to 'UK'.
    try:
        from cities import canonical_country
        key = canonical_country(country) if country else None
    except Exception:
        key = (country or "").strip().upper() or None

    return COUNTRY_TZ.get(key) if key else None


# ── Business hours config (env-driven) ───────────────────────────────
def _hour_bounds() -> tuple[int, int]:
    return (
        int(os.getenv("SEND_HOUR_START", "9")),
        int(os.getenv("SEND_HOUR_END",   "17")),
    )


def _weekdays_only() -> bool:
    return os.getenv("SEND_WEEKDAYS_ONLY", "true").lower() in ("1", "true", "yes")


def is_business_hours(lead: dict) -> bool:
    """True if it's currently a business hour in the lead's local timezone.

    Returns True if the timezone is unknown — better to send than to defer
    forever waiting for a window we can't compute.
    """
    tz_name = tz_for(lead.get("country"), lead.get("city"))
    if not tz_name:
        return True
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return True

    now_local = datetime.now(tz)
    if _weekdays_only() and now_local.weekday() >= 5:   # Sat=5, Sun=6
        return False
    h_start, h_end = _hour_bounds()
    return h_start <= now_local.hour < h_end


def next_business_hour(lead: dict) -> datetime:
    """Next 9am business-day datetime in the lead's local TZ, returned
    as a naive UTC datetime so it can be stored in scheduled_sends.scheduled_at.
    """
    tz_name = tz_for(lead.get("country"), lead.get("city")) or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = timezone.utc

    h_start, _ = _hour_bounds()
    now_local = datetime.now(tz)
    target = now_local.replace(hour=h_start, minute=0, second=0, microsecond=0)
    # If we're already past today's start hour, jump to tomorrow.
    if now_local >= target:
        target += timedelta(days=1)
    if _weekdays_only():
        while target.weekday() >= 5:
            target += timedelta(days=1)

    # Convert to naive UTC for DB storage.
    return target.astimezone(timezone.utc).replace(tzinfo=None)
