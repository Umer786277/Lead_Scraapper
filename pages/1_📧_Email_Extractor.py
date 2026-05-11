"""
Email Extractor — domain → emails, with MX verification and heuristic ranking.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Make sibling modules importable when run via `streamlit run`
sys.path.insert(0, str(Path(__file__).parent.parent))

import db
from email_extractor import normalize_domain, run_extraction

st.set_page_config(page_title="Email Extractor", page_icon="📧", layout="wide")
db.init_db()

# ── Shared styling ─────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; max-width: 1200px; }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #1a1d2e 0%, #111321 100%); }

    .hero {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        border-radius: 18px;
        padding: 1.75rem 2.25rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 10px 40px rgba(102,126,234,0.25);
    }
    .hero h1 { color: #fff; margin: 0; font-size: 1.9rem; }
    .hero p  { color: rgba(255,255,255,0.85); margin: 0.4rem 0 0 0; }

    .pill {
        display: inline-block; padding: 0.2rem 0.65rem;
        border-radius: 999px; font-size: 0.78rem; font-weight: 600;
    }
    .pill-high { background: rgba(34,197,94,0.18);  color: #4ade80; }
    .pill-med  { background: rgba(234,179,8,0.18);  color: #facc15; }
    .pill-low  { background: rgba(148,163,184,0.18); color: #cbd5e1; }

    .stButton > button[kind="primary"] {
        background: linear-gradient(135deg, #667eea, #764ba2);
        border: none; padding: 0.7rem 1.8rem; font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# ── Hero ───────────────────────────────────────────────────
st.markdown("""
<div class="hero">
    <h1>📧  Email Extractor</h1>
    <p>Paste domains, get emails. Uses contact-page scraping + MX validation + pattern-guess fallback. All results saved to the admin DB.</p>
</div>
""", unsafe_allow_html=True)

# ── Input ──────────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️  Settings")
    headless = st.toggle("Headless browser", value=True, help="Uncheck to watch Chromium work.")
    st.caption("Browser is shared across domains for speed.")

col_in, col_ex = st.columns([3, 1])
with col_in:
    domains_input = st.text_area(
        "Domains to scrape (one per line)",
        "thelondonvet.co.uk\nhellovet.co.uk\nanimal-clinic.co.uk",
        height=180,
        help="Paste raw domains or full URLs — we'll normalize them.",
    )
with col_ex:
    st.markdown("##### Preview")
    domains = [normalize_domain(d) for d in domains_input.splitlines() if d.strip()]
    domains = [d for d in domains if d]
    st.metric("Domains", len(domains))
    if domains:
        st.caption("First 5:")
        for d in domains[:5]:
            st.code(d, language="text")

run_clicked = st.button(
    "🚀  Extract emails",
    type="primary",
    disabled=not domains,
    use_container_width=False,
)

# ── Run ────────────────────────────────────────────────────
if run_clicked and domains:
    run_id = db.start_run("email_extractor", input_count=len(domains),
                          metadata={"headless": headless})

    progress = st.progress(0.0, text="Starting...")
    log_box = st.empty()
    results_box = st.empty()

    log_lines = []
    running_results = []

    def on_progress(idx, total, domain, result):
        emails = result["emails"]
        high = sum(1 for e in emails if e["confidence"] == "high")
        icon = "✅" if high else ("⚠️" if emails else "❌")
        line = f"{icon}  {domain}  ·  {len(emails)} email(s), {high} high-confidence"
        if result["error"]:
            line += f"  ·  {result['error']}"
        log_lines.append(line)

        progress.progress(idx / total, text=f"[{idx}/{total}] {domain}")
        log_box.code("\n".join(log_lines[-20:]), language="text")

        # Persist to DB incrementally
        domain_id = db.insert_domain(
            run_id=run_id,
            domain=domain,
            status="scraped" if not result["error"] else ("no_mx" if result["error"] == "no_mx" else "failed"),
            has_mx=result["has_mx"],
            pages_visited=result["pages_visited"],
            emails_found=len(emails),
            error=result["error"],
        )
        for e in emails:
            db.insert_email(
                domain_id=domain_id,
                domain=domain,
                email=e["email"],
                source_url=e["source_url"],
                source_type=e["source_type"],
                confidence=e["confidence"],
                is_role=e["is_role"],
                has_mx=result["has_mx"],
            )
            # Also create a Lead row for each email (pipeline-ready)
            db.insert_lead(
                source="email_extractor",
                run_id=run_id,
                domain=domain,
                email=e["email"],
                website=f"https://{domain}",
                status="new",
            )
            running_results.append({
                "domain":     domain,
                "email":      e["email"],
                "confidence": e["confidence"],
                "source":     e["source_type"],
                "role":       "yes" if e["is_role"] else "no",
                "has_mx":     "yes" if result["has_mx"] else "no",
            })

        if running_results:
            results_box.dataframe(
                pd.DataFrame(running_results),
                use_container_width=True,
                hide_index=True,
            )

    try:
        run_extraction(domains, on_progress=on_progress, headless=headless)
        db.finish_run(run_id, output_count=len(running_results), status="completed")
        progress.progress(1.0, text="Done.")
        st.success(f"✅  Extracted {len(running_results)} email(s) across {len(domains)} domain(s).")
    except Exception as e:
        db.finish_run(run_id, output_count=len(running_results), status="failed")
        progress.empty()
        st.error(f"❌  Extraction failed: {e}")

    if running_results:
        df = pd.DataFrame(running_results)
        st.download_button(
            "📥  Download CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name=f"emails_run_{run_id}.csv",
            mime="text/csv",
        )

# ── Previous runs ──────────────────────────────────────────
st.divider()
st.subheader("Previous runs")
runs = [r for r in db.runs_summary(limit=20) if r["run_type"] == "email_extractor"]
if not runs:
    st.caption("_No email-extractor runs yet._")
else:
    st.dataframe(
        pd.DataFrame(runs)[["id", "status", "started_at", "finished_at", "input_count", "output_count"]],
        use_container_width=True,
        hide_index=True,
    )
