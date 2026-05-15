"""
============================================================
  Google Maps Lead Scraper — Playwright (No API Key Needed)
  Works 100% free — scrapes like a real browser
============================================================

SETUP (run these once):
  pip install playwright pandas openpyxl
  playwright install chromium

RUN:
  python google_maps_scraper.py
============================================================
"""

from playwright.sync_api import sync_playwright
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode, unquote
import pandas as pd
import time
import re
from datetime import datetime

# ============================================================
#  CONFIG — Edit this section before running
# ============================================================

SEARCHES = [
    {"niche": "pet clinics",      "city": "London",     "country": "UK"},
    # {"niche": "law firm",           "city": "Karachi",    "country": "Pakistan"},
    # {"niche": "real estate agency", "city": "Islamabad",  "country": "Pakistan"},
    # {"niche": "HVAC repair",        "city": "Lahore",     "country": "Pakistan"},
    # {"niche": "dental clinic",      "city": "Dubai",      "country": "UAE"},
    # Add more searches below:
    # {"niche": "accounting firm", "city": "Lahore", "country": "Pakistan"},
]

MAX_LEADS_PER_SEARCH = 50    # How many leads per search (max ~60)
OUTPUT_FILE = "leads_output.xlsx"
HEADLESS = True              # Set False to watch the browser (good for debugging)

# ============================================================


def scroll_results_panel(page, max_leads):
    """Scroll the left panel to load more results."""
    scrollable = page.query_selector('div[role="feed"]')
    if not scrollable:
        return
    prev_count = 0
    stale_rounds = 0
    for _ in range(15):
        scrollable.evaluate('(el) => { el.scrollTop = el.scrollHeight; }')
        page.wait_for_timeout(900)
        items = page.query_selector_all('div[role="feed"] a.hfpxzc')
        if len(items) >= max_leads:
            break
        if len(items) == prev_count:
            stale_rounds += 1
            if stale_rounds >= 2:
                break
        else:
            stale_rounds = 0
        prev_count = len(items)


def clean_website_url(url):
    """Unwrap Google redirect URLs and strip utm_/gclid/fbclid tracking params."""
    if not url:
        return ""

    # Unwrap google.com/url?q=<real>
    if "google.com/url" in url:
        m = re.search(r'[?&]q=([^&]+)', url)
        if m:
            url = unquote(m.group(1))

    try:
        parts = urlparse(url)
    except Exception:
        return url

    if not parts.query:
        return url

    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith("utm_") or k.lower() in {"gclid", "fbclid", "mc_cid", "mc_eid"})
    ]
    return urlunparse(parts._replace(query=urlencode(kept)))


def extract_phone(text):
    """Extract phone number from text using regex."""
    if not text:
        return ""
    patterns = [
        r'\+?[\d\s\-\(\)]{10,18}',
        r'[\+\(]?[0-9][0-9 \-\(\)]{8,}[0-9]',
    ]
    for p in patterns:
        match = re.search(p, text)
        if match:
            phone = match.group().strip()
            if len(re.sub(r'\D', '', phone)) >= 7:
                return phone
    return ""


def _parse_days_ago(text: str):
    """Convert Google Maps relative date text to approximate integer days."""
    if not text:
        return None
    t = text.lower().strip()
    m = re.search(r'(\d+)', t)
    num = int(m.group(1)) if m else 1   # "a week ago" has no digit → 1 unit
    if "year"  in t: return num * 365
    if "month" in t: return num * 30
    if "week"  in t: return num * 7
    if "day"   in t: return num
    if "hour"  in t or "minute" in t or "just now" in t: return 0
    return None


def scrape_place_details(page, url):
    """Open a single business listing and extract all details."""
    details = {
        "phone": "",
        "website": "",
        "address": "",
        "rating": "",
        "reviews": "",
        "status": "",
        "maps_url": url,
        "last_review_days": None,
    }

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector('h1.DUwDvf, h1[data-attrid="title"]', timeout=8000)
        except Exception:
            pass

        # Business name
        try:
            name_el = page.query_selector('h1.DUwDvf, h1[data-attrid="title"]')
            details["name"] = name_el.inner_text().strip() if name_el else ""
        except:
            details["name"] = ""

        # Rating
        try:
            rating_el = page.query_selector('div.F7nice span[aria-hidden="true"]')
            if not rating_el:
                rating_el = page.query_selector('span.ceNzKf')
            details["rating"] = rating_el.inner_text().strip() if rating_el else ""
        except:
            pass

        # Review count
        try:
            review_el = page.query_selector('div.F7nice span[aria-label*="review"]')
            if review_el:
                reviews_text = review_el.get_attribute("aria-label") or ""
                numbers = re.findall(r'[\d,]+', reviews_text)
                details["reviews"] = numbers[0].replace(",", "") if numbers else ""
        except:
            pass

        # Address
        try:
            addr_els = page.query_selector_all('button[data-item-id="address"] div.fontBodyMedium')
            if addr_els:
                details["address"] = addr_els[0].inner_text().strip()
        except:
            pass

        # Phone number — only trust the structured button; body-scan picks up postcodes
        try:
            phone_els = page.query_selector_all('button[data-item-id*="phone"] div.fontBodyMedium')
            if phone_els:
                details["phone"] = phone_els[0].inner_text().strip()
        except:
            pass

        # Website
        try:
            web_el = page.query_selector('a[data-item-id="authority"]')
            if web_el:
                details["website"] = clean_website_url(web_el.get_attribute("href") or "")
        except:
            pass

        # Most recent review date
        try:
            date_els = page.query_selector_all('span.rsqaWe, span.dehysf')
            if date_els:
                details["last_review_days"] = _parse_days_ago(date_els[0].inner_text())
        except:
            pass

        # Business status (open/closed)
        try:
            status_selectors = [
                'span.ZDu9vd span',
                'div[data-hide-tooltip-on-mouse-out] span.ZDu9vd',
                'div.o0Xue',
                'div[jsaction*="openhours"] span[aria-label]',
            ]
            status_text = ""
            for sel in status_selectors:
                el = page.query_selector(sel)
                if el:
                    status_text = (el.inner_text() or el.get_attribute("aria-label") or "").strip()
                    if status_text:
                        break
            details["status"] = status_text or "Unknown"
        except:
            details["status"] = "Unknown"

    except Exception as e:
        print(f"    ⚠️  Error extracting details: {e}")

    return details


def search_google_maps(niche, city, country, max_leads, page):
    """Search Google Maps and collect lead URLs."""
    query = f"{niche} in {city}, {country}"
    search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"

    print(f"\n🔍 Searching: {query}")
    print(f"   URL: {search_url}")

    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)

        # Handle consent/cookie popup if it appears
        try:
            consent_btn = page.query_selector('button[aria-label*="Accept"], form[action*="consent"] button')
            if consent_btn:
                consent_btn.click()
        except:
            pass

        try:
            page.wait_for_selector('div[role="feed"] a.hfpxzc', timeout=10000)
        except Exception:
            pass

        # Scroll to load more results
        scroll_results_panel(page, max_leads)

        # Collect all listing URLs
        listing_links = page.query_selector_all('a.hfpxzc')
        if not listing_links:
            listing_links = page.query_selector_all('div[role="feed"] a[href*="/maps/place/"]')

        urls = []
        seen = set()
        for link in listing_links:
            href = link.get_attribute("href")
            if href and "/maps/place/" in href and href not in seen:
                seen.add(href)
                urls.append(href)
                if len(urls) >= max_leads:
                    break

        print(f"   Found {len(urls)} listing URLs")
        return urls

    except Exception as e:
        print(f"   ❌ Search failed: {e}")
        return []


def run_scraper(searches=None, max_leads=None, output_file=None, headless=None):
    searches = SEARCHES if searches is None else searches
    max_leads = MAX_LEADS_PER_SEARCH if max_leads is None else max_leads
    output_file = OUTPUT_FILE if output_file is None else output_file
    headless = HEADLESS if headless is None else headless

    # Persist to SQLite too (soft import — CLI still works if db.py isn't present)
    try:
        import db as _db
        _db.init_db()
        run_id = _db.start_run(
            "google_maps",
            input_count=len(searches),
            metadata={"max_leads": max_leads, "headless": headless},
        )
    except Exception:
        _db = None
        run_id = None

    all_leads = []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )

        # Hide automation flags
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = { runtime: {} };
        """)

        # Skip heavy assets we don't need — map tiles, images, fonts, media.
        # Huge speedup on Maps without affecting the text data we scrape.
        def _block_heavy(route):
            rtype = route.request.resource_type
            if rtype in ("image", "media", "font"):
                return route.abort()
            url = route.request.url
            if "googleusercontent.com" in url or "/vt/" in url or "/maps/vt" in url:
                return route.abort()
            return route.continue_()

        context.route("**/*", _block_heavy)

        search_page = context.new_page()
        detail_page = context.new_page()

        for config in searches:
            niche   = config["niche"]
            city    = config["city"]
            country = config["country"]

            urls = search_google_maps(niche, city, country, max_leads, search_page)

            if not urls:
                print(f"   ⚠️  No results found for this search")
                continue

            for i, url in enumerate(urls, 1):
                print(f"   [{i}/{len(urls)}] Extracting details...", end=" ")
                details = scrape_place_details(detail_page, url)

                name = details.get("name", "")
                phone = details.get("phone", "")

                lead = {
                    "Business Name": name,
                    "Niche":         niche,
                    "City":          city,
                    "Country":       country,
                    "Phone":         phone,
                    "Website":       details.get("website", ""),
                    "Address":       details.get("address", ""),
                    "Rating":        details.get("rating", ""),
                    "Total Reviews": details.get("reviews", ""),
                    "Open Status":   details.get("status", ""),
                    "Google Maps":   url,
                    "Scraped At":    timestamp,
                }

                all_leads.append(lead)
                status_icon = "📞" if phone else "❌"
                print(f"{name[:35]:<35} | {status_icon} {phone or 'No phone'}")

                # Persist to DB incrementally
                if _db and run_id:
                    try:
                        website = details.get("website", "") or ""
                        domain = ""
                        if website:
                            try:
                                from urllib.parse import urlparse as _urlparse
                                domain = _urlparse(website).netloc.lower().lstrip("www.")
                            except Exception:
                                domain = ""
                        rating_val = None
                        try:
                            rating_val = float(details.get("rating", "")) if details.get("rating") else None
                        except ValueError:
                            rating_val = None
                        reviews_val = None
                        try:
                            reviews_val = int(str(details.get("reviews", "")).replace(",", "")) if details.get("reviews") else None
                        except ValueError:
                            reviews_val = None
                        _db.insert_lead(
                            source="google_maps",
                            run_id=run_id,
                            business_name=name,
                            domain=domain or None,
                            phone=phone or None,
                            website=website or None,
                            address=details.get("address") or None,
                            city=city,
                            country=country,
                            rating=rating_val,
                            reviews=reviews_val,
                            status="new",
                        )
                    except Exception as _e:
                        print(f"    (db write skipped: {_e})")

                # Small polite delay
                time.sleep(0.3)

        browser.close()

    # ── Save results ────────────────────────────────────────
    if not all_leads:
        print("\n⚠️  No leads were collected.")
        if _db and run_id:
            _db.finish_run(run_id, output_count=0, status="completed")
        return

    df = pd.DataFrame(all_leads)

    # Dedup on the Maps URL (stable per-listing); fall back to name if URL missing
    before = len(df)
    df.drop_duplicates(subset=["Google Maps"], keep="first", inplace=True)
    after = len(df)

    # Sort by rating
    df["Rating"] = pd.to_numeric(df["Rating"], errors="coerce")
    df.sort_values("Rating", ascending=False, inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Save Excel with one sheet per niche
    with pd.ExcelWriter(OUTPUT_FILE, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="All Leads")
        for niche in df["Niche"].unique():
            sheet_df = df[df["Niche"] == niche]
            sheet_name = niche[:31]
            sheet_df.to_excel(writer, index=False, sheet_name=sheet_name)

    print(f"\n{'='*55}")
    print(f"✅  DONE! Saved {after} leads to '{OUTPUT_FILE}'")
    print(f"🧹  Removed {before - after} duplicates")
    print(f"\n📊  Breakdown by niche:")
    for niche in df["Niche"].unique():
        count = len(df[df["Niche"] == niche])
        with_phone = len(df[(df["Niche"] == niche) & (df["Phone"] != "")])
        print(f"    {niche}: {count} leads ({with_phone} with phone)")
    print(f"{'='*55}")

    if _db and run_id:
        _db.finish_run(run_id, output_count=after, status="completed")

    return df


if __name__ == "__main__":
    run_scraper()
