"""
Domain → email extractor.

No paid APIs: Playwright visits common contact pages, pulls emails from
mailto: links and body text, falls back to pattern guessing, and validates
the domain's MX record via dnspython.

Public API:
    extract_from_domain(domain: str, context) -> dict
    run_extraction(domains: list[str], on_progress=None) -> list[dict]

Install once:
    pip install dnspython
    playwright install chromium
"""

import asyncio
import re
import sys
from urllib.parse import urljoin, urlparse

# Streamlit ships Tornado, which on Windows sets WindowsSelectorEventLoopPolicy —
# and that policy can't spawn subprocesses, which Playwright requires.
# Force Proactor for any *new* event loops (Tornado's existing loop is unaffected).
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

try:
    import dns.resolver
    _DNS_AVAILABLE = True
except ImportError:
    _DNS_AVAILABLE = False

from playwright.sync_api import sync_playwright

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Pages to try per domain — ordered by contact relevance.
# Personal named emails live on team/about pages, so we crawl those too.
CONTACT_PATHS = [
    "/", "/contact", "/contact-us", "/contact.html",
    "/about", "/about-us", "/team", "/our-team", "/people",
    "/staff", "/leadership", "/management",
]

# Role-based prefixes (flagged separately — good for cold outreach backup,
# but usually lower reply rate than a named person).
ROLE_PREFIXES = {
    "info", "contact", "sales", "support", "hello", "admin",
    "enquiries", "office", "team", "help", "mail", "general",
    "reception", "bookings", "appointments",
}

# Heuristic: a "personal_named" local part has a separator (john.smith,
# j_smith, john-smith) and isn't a role prefix. Single-token locals like
# "john" fall into "personal" — they could be a person or a single-owner alias.
_NAMED_RE = re.compile(r"^[a-z]{2,}[._-][a-z]{2,}", re.IGNORECASE)

# Generic guesses when nothing was scraped — low confidence, don't send
# without manual review.
PATTERN_GUESSES = ["info", "contact", "hello", "sales"]

# Junk patterns that sneak through the regex but aren't real emails.
JUNK_RE = re.compile(
    r"(?:example\.com|sentry\.io|yourdomain|wixpress|cloudfront|"
    r"\.png|\.jpg|\.jpeg|\.gif|\.webp|\.svg|\.css|\.js)$",
    re.IGNORECASE,
)


def normalize_domain(raw):
    """Turn anything the user pastes into a bare 'example.com'."""
    raw = raw.strip().lower()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = urlparse(raw).netloc or urlparse(raw).path
    return host.lstrip("www.").split("/")[0]


def has_mx(domain):
    if not _DNS_AVAILABLE:
        return None
    try:
        answers = dns.resolver.resolve(domain, "MX", lifetime=5)
        return bool(list(answers))
    except Exception:
        return False


def _is_role(email):
    local = email.split("@")[0].lower()
    return any(local == p or local.startswith(p + ".") for p in ROLE_PREFIXES)


def _classify(email, source_type):
    """Return one of: personal_named, personal, role, pattern_guess.

    Reply-rate research suggests firstname.lastname@ inboxes outperform
    role inboxes by ~2x, so we surface this category for downstream scoring.
    """
    if source_type == "pattern_guess":
        return "pattern_guess"
    if _is_role(email):
        return "role"
    local = email.split("@")[0]
    if _NAMED_RE.match(local):
        return "personal_named"
    return "personal"


def _is_junk(email):
    return bool(JUNK_RE.search(email))


def _same_or_related_domain(email, target_domain):
    """Prefer emails that actually live on the target domain."""
    try:
        addr_domain = email.split("@", 1)[1].lower()
    except IndexError:
        return False
    return target_domain in addr_domain or addr_domain in target_domain


def _scrape_page(page, url, capture_snippet=False):
    """Visit a URL, return (mailto_emails, text_emails, snippet).

    snippet is None unless capture_snippet=True, in which case we collect
    title + first ~400 chars of meaningful body text for the personalizer.
    """
    mailto_emails = set()
    text_emails = set()
    snippet = None
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=12000)
        page.wait_for_timeout(500)

        # mailto: anchors — highest confidence signal
        for a in page.query_selector_all('a[href^="mailto:"]'):
            href = a.get_attribute("href") or ""
            raw = href[len("mailto:"):].split("?")[0].strip()
            for candidate in raw.split(","):
                candidate = candidate.strip()
                if candidate and EMAIL_RE.fullmatch(candidate):
                    mailto_emails.add(candidate.lower())

        # body text — medium confidence
        body = page.inner_text("body")
        for match in EMAIL_RE.findall(body):
            text_emails.add(match.lower())

        if capture_snippet:
            try:
                title = (page.title() or "").strip()
            except Exception:
                title = ""
            cleaned = " ".join((body or "").split())[:400]
            snippet = (f"{title} | {cleaned}" if title else cleaned).strip() or None
    except Exception:
        pass
    return mailto_emails, text_emails, snippet


def extract_from_domain(domain, context):
    """Scrape a single domain across all known contact paths.

    Returns:
        {
            "domain": str,
            "has_mx": bool | None,
            "emails": [ {email, source_url, source_type, confidence, is_role} ],
            "pages_visited": int,
            "error": str | None,
        }
    """
    result = {
        "domain": domain,
        "has_mx": None,
        "emails": [],
        "pages_visited": 0,
        "snippet": None,        # captured from homepage for LLM personalization
        "error": None,
    }

    mx = has_mx(domain)
    result["has_mx"] = mx

    # If no MX, don't waste time scraping — just pattern-guess for record-keeping.
    if mx is False:
        for prefix in PATTERN_GUESSES:
            email = f"{prefix}@{domain}"
            result["emails"].append({
                "email":       email,
                "source_url":  None,
                "source_type": "pattern_guess",
                "confidence":  "low",
                "is_role":     True,
                "category":    _classify(email, "pattern_guess"),
            })
        result["error"] = "no_mx"
        return result

    page = context.new_page()
    found = {}  # email -> best metadata (dedupe, keep highest confidence)

    def _add(email, url, source_type, confidence):
        if _is_junk(email):
            return
        is_role = _is_role(email)
        category = _classify(email, source_type)
        existing = found.get(email)
        rank = {"high": 3, "medium": 2, "low": 1}
        if existing is None or rank[confidence] > rank[existing["confidence"]]:
            found[email] = {
                "email":       email,
                "source_url":  url,
                "source_type": source_type,
                "confidence":  confidence,
                "is_role":     is_role,
                "category":    category,
            }

    try:
        for path in CONTACT_PATHS:
            url = f"https://{domain}{path}"
            # Capture a snippet on the homepage hit only — that's the most
            # representative page for personalization.
            mailto_emails, text_emails, snippet = _scrape_page(
                page, url, capture_snippet=(path == "/")
            )
            if snippet and not result["snippet"]:
                result["snippet"] = snippet
            if mailto_emails or text_emails:
                result["pages_visited"] += 1

            for email in mailto_emails:
                conf = "high" if _same_or_related_domain(email, domain) else "medium"
                _add(email, url, "mailto", conf)

            for email in text_emails:
                if not _same_or_related_domain(email, domain):
                    continue  # skip random third-party emails in page text
                _add(email, url, "text", "medium")

            # Early stop: got some high-confidence hits already
            high_hits = sum(1 for v in found.values() if v["confidence"] == "high")
            if high_hits >= 3:
                break
    except Exception as e:
        result["error"] = str(e)[:200]
    finally:
        try:
            page.close()
        except Exception:
            pass

    # Pattern-guess fallback if nothing was found
    if not found:
        for prefix in PATTERN_GUESSES:
            _add(f"{prefix}@{domain}", None, "pattern_guess", "low")

    result["emails"] = list(found.values())
    return result


def run_extraction(domains, on_progress=None, headless=True):
    """
    Run extraction across a list of domains. Yields progress callbacks.

    Args:
        domains: list of raw domain strings (will be normalized)
        on_progress: callable(index, total, domain, result) — called after each domain
        headless: run browser headless

    Returns:
        list of per-domain result dicts
    """
    clean = [d for d in (normalize_domain(x) for x in domains) if d]
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        # Block heavy assets — same trick as the maps scraper
        def _block(route):
            if route.request.resource_type in ("image", "media", "font"):
                return route.abort()
            return route.continue_()
        context.route("**/*", _block)

        for i, domain in enumerate(clean, 1):
            res = extract_from_domain(domain, context)
            results.append(res)
            if on_progress:
                on_progress(i, len(clean), domain, res)

        browser.close()

    return results
