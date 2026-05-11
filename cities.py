"""
City lookup for the scrape rotation worker.

Strategy (hybrid):
  1. Static curated list — covers the 6 most common SaaS-target countries
     (UK, US, AU, CA, IE, NZ). Zero cost, instant.
  2. Cache table `country_cities` — for countries fetched at runtime.
  3. LLM fallback — if neither hit, call gpt-4o-mini once for the country,
     persist to cache, return.

Public API:
    cities_for(country: str) -> list[str]
    canonical_country(country: str) -> str | None  (for known static keys)
"""

import json

import db

CITIES_BY_COUNTRY: dict[str, list[str]] = {
    "UK": [
        "London", "Manchester", "Birmingham", "Leeds", "Glasgow", "Liverpool",
        "Bristol", "Sheffield", "Edinburgh", "Cardiff", "Belfast", "Nottingham",
        "Newcastle", "Leicester", "Coventry", "Bradford", "Stoke-on-Trent",
        "Wolverhampton", "Plymouth", "Southampton", "Reading", "Derby",
        "Brighton", "Aberdeen", "Portsmouth", "York", "Oxford", "Cambridge",
        "Norwich", "Swansea",
    ],
    "US": [
        "New York", "Los Angeles", "Chicago", "Houston", "Phoenix",
        "Philadelphia", "San Antonio", "San Diego", "Dallas", "San Jose",
        "Austin", "Jacksonville", "Fort Worth", "Columbus", "Charlotte",
        "Indianapolis", "San Francisco", "Seattle", "Denver", "Washington DC",
        "Boston", "Nashville", "Baltimore", "Portland", "Las Vegas",
        "Memphis", "Louisville", "Milwaukee", "Atlanta", "Miami",
    ],
    "AU": [
        "Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Gold Coast",
        "Newcastle", "Canberra", "Sunshine Coast", "Wollongong", "Hobart",
        "Geelong", "Townsville", "Cairns", "Darwin", "Toowoomba", "Ballarat",
        "Bendigo", "Launceston", "Mackay",
    ],
    "CA": [
        "Toronto", "Montreal", "Vancouver", "Calgary", "Edmonton", "Ottawa",
        "Winnipeg", "Quebec City", "Hamilton", "Kitchener", "London",
        "Victoria", "Halifax", "Oshawa", "Windsor", "Saskatoon", "Regina",
        "Sherbrooke", "St. John's", "Barrie",
    ],
    "IE": [
        "Dublin", "Cork", "Galway", "Limerick", "Waterford", "Drogheda",
        "Dundalk", "Bray", "Navan", "Ennis", "Kilkenny", "Tralee", "Sligo",
        "Wexford", "Athlone",
    ],
    "NZ": [
        "Auckland", "Wellington", "Christchurch", "Hamilton", "Tauranga",
        "Dunedin", "Palmerston North", "Napier", "Hastings", "Nelson",
        "Rotorua", "New Plymouth", "Whangarei", "Invercargill", "Queenstown",
    ],
}

# Aliases that users might type → canonical static key
_ALIASES = {
    "UNITED KINGDOM": "UK", "GB": "UK", "GBR": "UK", "ENGLAND": "UK",
    "SCOTLAND": "UK", "WALES": "UK", "NORTHERN IRELAND": "UK", "BRITAIN": "UK",
    "USA": "US", "UNITED STATES": "US", "AMERICA": "US",
    "AUSTRALIA": "AU", "AUS": "AU",
    "CANADA": "CA", "CAN": "CA",
    "IRELAND": "IE", "IRL": "IE",
    "NEW ZEALAND": "NZ", "NZL": "NZ",
}


def canonical_country(country: str) -> str | None:
    """Normalize a free-text country to a static-table key, if known."""
    if not country:
        return None
    key = country.strip().upper()
    if key in CITIES_BY_COUNTRY:
        return key
    return _ALIASES.get(key)


def _cache_get(country_key: str) -> list[str] | None:
    with db.get_conn() as c:
        row = c.execute(
            "SELECT cities FROM country_cities WHERE country = %s",
            (country_key,),
        ).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row["cities"])
        return data if isinstance(data, list) else None
    except (json.JSONDecodeError, TypeError):
        return None


def _cache_put(country_key: str, cities: list[str]) -> None:
    with db.get_conn() as c:
        c.execute(
            "INSERT INTO country_cities (country, cities) VALUES (%s, %s) "
            "ON CONFLICT (country) DO UPDATE "
            "SET cities = EXCLUDED.cities, fetched_at = CURRENT_TIMESTAMP",
            (country_key, json.dumps(cities)),
        )


def cities_for(country: str) -> list[str]:
    """Return curated/cached/LLM-fetched cities for a country.

    Order: static map → cache table → LLM (and persist). Returns [] if
    even the LLM fails (no API key, network error, malformed response).
    """
    if not country:
        return []

    # 1) Static fast path
    static_key = canonical_country(country)
    if static_key:
        return list(CITIES_BY_COUNTRY[static_key])

    # 2) Cache (by lowercased free-text input)
    cache_key = country.strip().lower()
    cached = _cache_get(cache_key)
    if cached:
        return cached

    # 3) LLM fallback (lazy import — avoids loading openai client unless needed)
    try:
        from llm import fetch_cities
    except Exception:
        return []
    cities = fetch_cities(country)
    if cities:
        _cache_put(cache_key, cities)
    return cities
