"""
Scrape & Enrich — one-click Google Maps → email extraction pipeline.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
from pipeline import run_pipeline

st.set_page_config(page_title="Scrape & Enrich", page_icon="🚀", layout="wide")
db.init_db()

# ── Styling ─────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; max-width: 1200px; }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #1a1d2e 0%, #111321 100%); }

    .hero {
        background: linear-gradient(135deg, #f97316 0%, #ef4444 100%);
        border-radius: 18px;
        padding: 1.75rem 2.25rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 10px 40px rgba(249,115,22,0.25);
    }
    .hero h1 { color: #fff; margin: 0; font-size: 1.9rem; }
    .hero p  { color: rgba(255,255,255,0.85); margin: 0.4rem 0 0 0; }

    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #f97316, #ef4444);
        border: none; padding: 0.75rem 2rem; font-weight: 600;
        box-shadow: 0 4px 14px rgba(249,115,22,0.4);
    }
    .stButton > button[kind="primary"]:hover {
        box-shadow: 0 6px 20px rgba(249,115,22,0.55);
        transform: translateY(-1px);
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <h1>🚀  Scrape &amp; Enrich</h1>
    <p>One click: Google Maps → business leads → emails extracted from their domains. Saved straight to the admin DB.</p>
</div>
""", unsafe_allow_html=True)

# ── Search inputs ──────────────────────────────────────────
st.subheader("Searches")
st.caption("Add one or more niche / city / country rows. Each becomes a separate Google Maps query.")

default_df = pd.DataFrame([
    {"niche": "pet clinics", "city": "London", "country": "UK"},
])

searches_df = st.data_editor(
    default_df,
    num_rows="dynamic",
    use_container_width=True,
    column_config={
        "niche":   st.column_config.TextColumn("Niche",   required=True),
        "city":    st.column_config.TextColumn("City",    required=True),
        "country": st.column_config.TextColumn("Country", required=True),
    },
    key="searches_editor",
)

# ── Settings ───────────────────────────────────────────────
s1, s2, s3, s4 = st.columns([2, 1, 1, 1])
with s1:
    max_leads = st.slider("Max leads per search", 5, 60, 20)
with s2:
    enrich = st.toggle("Extract emails", value=True,
                       help="After scraping, visit each lead's website and pull emails.")
with s3:
    headless = st.toggle("Headless browser", value=True)
with s4:
    st.metric("Est. pages", len(searches_df) * max_leads + (len(searches_df) * max_leads if enrich else 0))

# ── Validate ───────────────────────────────────────────────
clean_searches = []
for _, row in searches_df.iterrows():
    niche   = str(row.get("niche",   "") or "").strip()
    city    = str(row.get("city",    "") or "").strip()
    country = str(row.get("country", "") or "").strip()
    if niche and city and country:
        clean_searches.append({"niche": niche, "city": city, "country": country})

st.divider()

# ── Run ────────────────────────────────────────────────────
col_btn, col_info = st.columns([1, 3])
with col_btn:
    run_clicked = st.button(
        "🚀  Run scrape + enrich",
        type="primary",
        disabled=not clean_searches,
        use_container_width=True,
    )
with col_info:
    if clean_searches:
        st.caption(
            f"_Will run **{len(clean_searches)}** search(es) × up to **{max_leads}** leads each"
            f"{' + email extraction' if enrich else ''}._"
        )
    else:
        st.caption("_Add at least one complete search row (niche + city + country)._")

if run_clicked:
    # Progress widgets
    progress = st.progress(0.0, text="Starting…")
    log_box  = st.empty()
    counters = st.empty()
    lines = []

    # Rough estimate for progress bar: 1 tick per maps-listing + 1 per domain
    est_total = len(clean_searches) * max_leads + (
        len(clean_searches) * max_leads if enrich else 0
    )
    step = [0]

    stats = {"leads": 0, "emails": 0, "domains": 0}

    def on_event(kind, message, **extra):
        lines.append(message)
        if kind == "lead":
            step[0] += 1
            stats["leads"] += 1
        elif kind == "enrich" and message.strip().startswith(("[", "✅", "❌")):
            step[0] += 1
        elif kind == "phase":
            pass

        frac = min(step[0] / max(est_total, 1), 0.98)
        progress.progress(frac, text=message[:80])
        log_box.code("\n".join(lines[-30:]), language="text")

        c1, c2, c3 = counters.columns(3)
        c1.metric("Leads",   stats["leads"])
        c2.metric("Emails",  stats["emails"])
        c3.metric("Domains", stats["domains"])

    try:
        result = run_pipeline(
            searches=clean_searches,
            max_leads=int(max_leads),
            headless=headless,
            enrich_emails=enrich,
            on_event=on_event,
        )
        stats["leads"]   = result["leads_created"]
        stats["emails"]  = result["emails_created"]
        stats["domains"] = result["domains_scanned"]

        progress.progress(1.0, text="Done")
        c1, c2, c3 = counters.columns(3)
        c1.metric("Leads",   stats["leads"])
        c2.metric("Emails",  stats["emails"])
        c3.metric("Domains", stats["domains"])

        st.success(
            f"✅  Run **#{result['run_id']}** complete — "
            f"{result['leads_created']} lead(s), "
            f"{result['emails_created']} email(s) extracted from "
            f"{result['domains_scanned']} domain(s)."
        )
    except Exception as e:
        progress.empty()
        st.error(f"❌  Pipeline failed: {e}")
        st.stop()

    # ── Results table ──────────────────────────────────────
    st.subheader(f"Leads from run #{result['run_id']}")
    with db.get_conn() as c:
        rows = c.execute(
            "SELECT id, business_name, domain, email, phone, website, city, country, "
            "rating, reviews, status "
            "FROM leads WHERE run_id=%s ORDER BY id DESC",
            (result["run_id"],),
        ).fetchall()

    if rows:
        df = pd.DataFrame([dict(r) for r in rows])

        m1, m2, m3 = st.columns(3)
        m1.metric("With email",   int((df["email"].fillna("") != "").sum()))
        m2.metric("With phone",   int((df["phone"].fillna("") != "").sum()))
        m3.metric("With website", int((df["website"].fillna("") != "").sum()))

        st.dataframe(df, use_container_width=True, hide_index=True, height=420)

        st.download_button(
            "📥  Download run CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name=f"run_{result['run_id']}_leads.csv",
            mime="text/csv",
        )
    else:
        st.caption("_No leads were captured in this run._")

# ── Previous runs ──────────────────────────────────────────
st.divider()
st.subheader("Previous scrape & enrich runs")
runs = [r for r in db.runs_summary(limit=30) if r["run_type"] == "scrape_and_enrich"]
if not runs:
    st.caption("_No previous pipeline runs yet._")
else:
    st.dataframe(
        pd.DataFrame(runs)[["id", "status", "started_at", "finished_at",
                            "input_count", "output_count"]],
        use_container_width=True, hide_index=True,
    )
