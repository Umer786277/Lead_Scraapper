"""
Combined pipeline: Google Maps scrape → email extraction per domain.

One Playwright browser handles both phases (shared context), one DB run
groups everything. Leads get their best-match email written back into the
`leads.email` column so the Outreach module picks them up immediately.
"""

import asyncio
import sys
from urllib.parse import urlparse

# Same Windows fix as email_extractor — Tornado forces Selector policy which
# can't spawn subprocesses. Override for any new event loops.
if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from playwright.sync_api import sync_playwright

import db
import locks
from email_extractor import extract_from_domain
from google_maps_scraper import scrape_place_details, search_google_maps


def _block_heavy(route):
    rtype = route.request.resource_type
    if rtype in ("image", "media", "font"):
        return route.abort()
    url = route.request.url
    if "googleusercontent.com" in url or "/vt/" in url or "/maps/vt" in url:
        return route.abort()
    return route.continue_()


def _domain_from_url(url):
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return ""


def _to_int(v):
    try:
        return int(str(v).replace(",", "")) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _to_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


_CONF_RANK = {"high": 3, "medium": 2, "low": 1}
_CATEGORY_RANK = {
    "personal_named": 15,   # firstname.lastname@ — highest reply rate
    "personal":       8,    # single-name locals like john@
    "role":           0,    # info@, contact@, sales@
    "pattern_guess": -10,   # synthesized fallback, never beats a real find
}


def _email_score(e):
    """Score for picking the single 'best' email for a lead."""
    conf = _CONF_RANK.get(e.get("confidence"), 0) * 10
    category_bonus = _CATEGORY_RANK.get(
        e.get("category"),
        -2 if e.get("is_role") else 0,   # fallback for older rows w/o category
    )
    source_bonus = 3 if e.get("source_type") == "mailto" else 0
    return conf + category_bonus + source_bonus


def run_pipeline(searches, max_leads=20, headless=True, enrich_emails=True, on_event=None):
    """
    Run the full pipeline:
      1. For each search → scrape Google Maps listings → insert into `leads`
      2. (optional) Group leads by domain → extract emails → write back best
         email to each lead's row.

    Args:
        searches: list[dict] with keys niche, city, country
        max_leads: max listings per search
        headless: run browser headless
        enrich_emails: whether to run phase 2
        on_event: callable(kind, message, **extra) for live UI updates
                  kind ∈ {'phase', 'search', 'lead', 'enrich', 'error', 'done'}

    Returns:
        {run_id, leads_created, emails_created, domains_scanned, leads: [...]}
    """
    def emit(kind, message, **extra):
        if on_event:
            try:
                on_event(kind, message, **extra)
            except Exception:
                pass

    run_id = db.start_run(
        "scrape_and_enrich",
        input_count=len(searches),
        metadata={
            "max_leads":      max_leads,
            "headless":       headless,
            "enrich_emails":  enrich_emails,
            "searches":       searches,
        },
    )

    leads_created = []
    emails_created = 0
    domains_scanned = 0

    try:
        with locks.browser_lock, sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-gpu",
                    "--no-first-run",
                    "--disable-extensions",
                    "--disable-default-apps",
                    "--disable-background-networking",
                    "--disable-sync",
                    "--mute-audio",
                    "--disable-features=TranslateUI",
                ],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1024, "height": 600},
                locale="en-US",
            )
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                window.chrome = { runtime: {} };
            """)
            context.route("**/*", _block_heavy)

            # ── Phase 1: scrape Google Maps ────────────────────────
            emit("phase", "🗺️  Scraping Google Maps")
            search_page = context.new_page()
            detail_page = context.new_page()

            for search in searches:
                niche   = search.get("niche", "").strip()
                city    = search.get("city", "").strip()
                country = search.get("country", "").strip()
                if not (niche and city and country):
                    continue

                emit("search", f"🔍 {niche} in {city}, {country}")
                urls = search_google_maps(niche, city, country, max_leads, search_page)
                emit("search", f"   Found {len(urls)} listing URL(s)")

                for i, url in enumerate(urls, 1):
                    try:
                        details = scrape_place_details(detail_page, url)
                    except Exception as e:
                        emit("error", f"   [{i}/{len(urls)}] scrape failed: {e}")
                        continue

                    name    = details.get("name", "")
                    phone   = details.get("phone", "") or ""
                    website = details.get("website", "") or ""
                    domain  = _domain_from_url(website)

                    lead_id = db.insert_lead(
                        source="google_maps",
                        run_id=run_id,
                        business_name=name or None,
                        domain=domain or None,
                        phone=phone or None,
                        website=website or None,
                        address=details.get("address") or None,
                        city=city,
                        country=country,
                        rating=_to_float(details.get("rating")),
                        reviews=_to_int(details.get("reviews")),
                        maps_url=url or None,
                        last_review_days=details.get("last_review_days"),
                        status="new",
                    )
                    leads_created.append({
                        "lead_id": lead_id,
                        "name":    name,
                        "domain":  domain,
                        "website": website,
                        "phone":   phone,
                        "niche":   niche,
                        "city":    city,
                    })
                    icon = "📞" if phone else "🏢"
                    emit("lead", f"   [{i}/{len(urls)}] {icon} {name or '(no name)'}"
                                 f"{' · ' + domain if domain else ''}",
                         lead_id=lead_id, domain=domain, phone=phone)

            try: search_page.close()
            except Exception: pass
            try: detail_page.close()
            except Exception: pass

            # ── Phase 2: extract emails per unique domain ───────────
            if enrich_emails and leads_created:
                # Group leads by domain so we don't re-scrape the same site
                by_domain = {}
                for lead in leads_created:
                    if lead["domain"]:
                        by_domain.setdefault(lead["domain"], []).append(lead["lead_id"])

                emit("phase", f"📧  Extracting emails from "
                              f"{len(by_domain)} unique domain(s)")

                for dom_idx, (domain, lead_ids) in enumerate(by_domain.items(), 1):
                    emit("enrich", f"   [{dom_idx}/{len(by_domain)}] {domain}")
                    try:
                        result = extract_from_domain(domain, context)
                    except Exception as e:
                        emit("error", f"      failed: {e}")
                        continue

                    domains_scanned += 1

                    status = ("scraped" if not result.get("error")
                              else ("no_mx" if result.get("error") == "no_mx" else "failed"))
                    domain_id = db.insert_domain(
                        run_id=run_id,
                        domain=domain,
                        status=status,
                        has_mx=result.get("has_mx"),
                        pages_visited=result.get("pages_visited", 0),
                        emails_found=len(result.get("emails", [])),
                        error=result.get("error"),
                    )

                    best_email = None
                    snippet = result.get("snippet")
                    has_mx = result.get("has_mx")

                    # Batch: all email inserts + lead updates in one connection
                    with db.get_conn() as c:
                        for e in result.get("emails", []):
                            try:
                                c.execute(
                                    "INSERT INTO emails "
                                    "(domain_id, domain, email, source_url, source_type, confidence, "
                                    " is_role, category, verification_status, extracted_at) "
                                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                                    "ON CONFLICT (domain, email) DO NOTHING",
                                    (
                                        domain_id, domain, e["email"],
                                        e.get("source_url"), e.get("source_type"),
                                        e.get("confidence"), int(bool(e.get("is_role"))),
                                        e.get("category"),
                                        "valid_mx" if has_mx else "no_mx",
                                        db.now(),
                                    ),
                                )
                                emails_created += 1
                            except Exception:
                                pass
                            if best_email is None or _email_score(e) > _email_score(best_email):
                                best_email = e

                        # Attach best email + homepage snippet back to lead rows
                        if best_email or snippet:
                            for lid in lead_ids:
                                if best_email:
                                    c.execute(
                                        "UPDATE leads SET email=%s "
                                        "WHERE id=%s AND (email IS NULL OR email='')",
                                        (best_email["email"], lid),
                                    )
                                if snippet:
                                    c.execute(
                                        "UPDATE leads SET homepage_snippet=%s "
                                        "WHERE id=%s AND (homepage_snippet IS NULL OR homepage_snippet='')",
                                        (snippet, lid),
                                    )
                    if best_email:
                        emit("enrich",
                             f"      ✅ {best_email['email']} "
                             f"({best_email['confidence']})",
                             email=best_email["email"])
                    else:
                        emit("enrich", "      ❌ no emails found")

            browser.close()

        db.finish_run(run_id, output_count=len(leads_created), status="completed")
        emit("done", f"Completed run #{run_id}")
    except Exception as e:
        db.finish_run(run_id, output_count=len(leads_created), status="failed")
        emit("error", f"❌ Pipeline failed: {e}")
        raise

    return {
        "run_id":          run_id,
        "leads_created":   len(leads_created),
        "emails_created":  emails_created,
        "domains_scanned": domains_scanned,
        "leads":           leads_created,
    }
