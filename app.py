"""
Admin panel home — overview stats + quick navigation.

Run:
    pip install streamlit pandas requests dnspython
    playwright install chromium
    streamlit run app.py
"""

import pandas as pd
import streamlit as st

import db

st.set_page_config(
    page_title="Leads Admin",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

db.init_db()

# ── Styling ─────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; max-width: 1200px; }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #1a1d2e 0%, #111321 100%); }

    .hero {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 18px;
        padding: 2rem 2.5rem;
        margin-bottom: 2rem;
        box-shadow: 0 10px 40px rgba(102,126,234,0.25);
    }
    .hero h1 { color: #fff; margin: 0; font-size: 2.2rem; letter-spacing: -0.02em; }
    .hero p  { color: rgba(255,255,255,0.82); margin: 0.5rem 0 0 0; font-size: 1.05rem; }

    .stat-card {
        background: rgba(255,255,255,0.04);
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px;
        padding: 1.2rem 1.4rem;
    }
    .stat-card .label { font-size: 0.85rem; opacity: 0.7; margin-bottom: 0.3rem; }
    .stat-card .value { font-size: 2rem; font-weight: 700; letter-spacing: -0.02em; }

    [data-testid="stMetricValue"] { font-size: 2rem; }
</style>
""", unsafe_allow_html=True)

# ── Hero ────────────────────────────────────────────────────
st.markdown("""
<div class="hero">
    <h1>🎯  Leads Admin Panel</h1>
    <p>Unified view across scrapers, email extraction, and outreach. Use the sidebar to jump into a module.</p>
</div>
""", unsafe_allow_html=True)

# ── Overview stats ──────────────────────────────────────────
stats = db.overview_stats()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total leads", f"{stats['leads_total']:,}")
c2.metric("Emails extracted", f"{stats['emails_total']:,}",
          delta=f"{stats['emails_high_conf']:,} high confidence", delta_color="off")
c3.metric("Domains scanned", f"{stats['domains_total']:,}")
c4.metric("Runs completed", f"{stats['runs_total']:,}")

st.divider()

# ── Module shortcuts ────────────────────────────────────────
st.subheader("Modules")

with st.container(border=True):
    st.markdown("### 🚀  Scrape & Enrich  ·  **main flow**")
    st.caption("One click: Google Maps → leads → emails extracted from their domains.")
    st.page_link("pages/0_🚀_Scrape_&_Enrich.py", label="Open →", use_container_width=True)

r1c1, r1c2, r1c3 = st.columns(3)
r2c1, r2c2 = st.columns(2)

with r1c1:
    with st.container(border=True):
        st.markdown("### 📧  Email Extractor")
        st.caption("Already have domains? Paste them → get emails. Standalone mode.")
        st.page_link("pages/1_📧_Email_Extractor.py", label="Open →", use_container_width=True)

with r1c2:
    with st.container(border=True):
        st.markdown("### ✉️  Outreach")
        st.caption("Templates, campaigns, follow-up sequencer, send queue.")
        st.page_link("pages/3_✉️_Outreach.py", label="Open →", use_container_width=True)

with r1c3:
    with st.container(border=True):
        st.markdown("### 🤝  Negotiator")
        st.caption("IMAP replies → AI drafts → human-approved sends.")
        st.page_link("pages/4_🤝_Negotiator.py", label="Open →", use_container_width=True)

with r2c1:
    with st.container(border=True):
        st.markdown("### 📊  Analytics")
        st.caption("Run history, email quality, domain breakdown.")
        st.page_link("pages/2_📊_Analytics.py", label="Open →", use_container_width=True)

with r2c2:
    with st.container(border=True):
        st.markdown("### 🗺️  Google Maps (CLI)")
        st.caption("Headless CLI variant — same scrape, good for scheduled runs.")
        st.code("python google_maps_scraper.py", language="bash")

st.divider()

# ── Recent runs ─────────────────────────────────────────────
st.subheader("Recent runs")
runs = db.runs_summary(limit=10)
if not runs:
    st.info("No runs yet. Head to **Email Extractor** to kick one off.")
else:
    df = pd.DataFrame(runs)
    df = df[["id", "run_type", "status", "started_at", "finished_at", "input_count", "output_count"]]
    st.dataframe(df, use_container_width=True, hide_index=True)
