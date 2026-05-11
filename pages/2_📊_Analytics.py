"""
Analytics dashboard — lead pipeline by contact readiness + extraction stats.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import db

st.set_page_config(page_title="Analytics", page_icon="📊", layout="wide")
db.init_db()

st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; max-width: 1300px; }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #1a1d2e 0%, #111321 100%); }

    .hero {
        background: linear-gradient(135deg, #0ea5e9 0%, #6366f1 100%);
        border-radius: 18px;
        padding: 1.75rem 2.25rem;
        margin-bottom: 1.75rem;
        box-shadow: 0 10px 40px rgba(99,102,241,0.25);
    }
    .hero h1 { color: #fff; margin: 0; font-size: 1.95rem; }
    .hero p  { color: rgba(255,255,255,0.85); margin: 0.4rem 0 0 0; }

    .bucket-card {
        border-radius: 14px;
        padding: 1rem 1.25rem 0.5rem;
        margin-bottom: 0.5rem;
    }
    .bc-call    { background: rgba(34,197,94,0.08);  border: 1px solid rgba(34,197,94,0.25); }
    .bc-email   { background: rgba(99,102,241,0.08); border: 1px solid rgba(99,102,241,0.25); }
    .bc-pending { background: rgba(251,191,36,0.08); border: 1px solid rgba(251,191,36,0.25); }
    .bc-none    { background: rgba(239,68,68,0.08);  border: 1px solid rgba(239,68,68,0.25);  }

    [data-testid="stMetricValue"] { font-size: 1.9rem; }
    [data-testid="stMetricLabel"] { font-size: 0.85rem; opacity: 0.7; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <h1>📊  Analytics</h1>
    <p>Leads segmented by contact readiness — see exactly who is ready for calling, emailing, or still needs extraction.</p>
</div>
""", unsafe_allow_html=True)

# ── Pull ALL leads once ─────────────────────────────────────
with db.get_conn() as c:
    all_leads = [dict(r) for r in c.execute(
        "SELECT id, source, business_name, domain, email, phone, website, "
        "       address, city, country, rating, reviews, status, created_at "
        "FROM leads ORDER BY id DESC"
    ).fetchall()]

df_all = pd.DataFrame(all_leads) if all_leads else pd.DataFrame()

# ── Segment helper ──────────────────────────────────────────
def has_val(series):
    return series.notna() & (series.astype(str).str.strip() != "")

if not df_all.empty:
    has_phone  = has_val(df_all["phone"])
    has_email  = has_val(df_all["email"])
    has_domain = has_val(df_all["domain"])

    df_call    = df_all[has_phone].copy()                          # ready to call
    df_email   = df_all[has_email].copy()                          # ready to email
    df_pending = df_all[~has_email &  has_domain].copy()           # domain, no email yet
    df_none    = df_all[~has_phone & ~has_email & ~has_domain].copy()  # no contact at all
else:
    df_call = df_email = df_pending = df_none = pd.DataFrame()

# ── Headline metrics ────────────────────────────────────────
stats = db.overview_stats()
h1, h2, h3, h4 = st.columns(4)
h1.metric("Total leads",     f"{stats['leads_total']:,}")
h2.metric("Emails extracted", f"{stats['emails_total']:,}")
h3.metric("Domains scanned",  f"{stats['domains_total']:,}")
h4.metric("High confidence",  f"{stats['emails_high_conf']:,}",
          delta=f"{(stats['emails_high_conf']/stats['emails_total']*100):.0f}% of emails"
                if stats['emails_total'] else "—",
          delta_color="off")

st.divider()

# ══════════════════════════════════════════════════════════════
# LEAD PIPELINE — four buckets
# ══════════════════════════════════════════════════════════════
st.subheader("Lead pipeline")

# Summary row — four KPI cards
k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown('<div class="bucket-card bc-call">', unsafe_allow_html=True)
    st.metric("📞  Ready to call",    len(df_call),
              delta=f"{len(df_call[has_val(df_call['email'])])} also have email" if not df_call.empty and "email" in df_call else None,
              delta_color="off")
    st.markdown('</div>', unsafe_allow_html=True)

with k2:
    st.markdown('<div class="bucket-card bc-email">', unsafe_allow_html=True)
    st.metric("📧  Ready to email",   len(df_email))
    st.markdown('</div>', unsafe_allow_html=True)

with k3:
    st.markdown('<div class="bucket-card bc-pending">', unsafe_allow_html=True)
    st.metric("🔍  Pending extraction", len(df_pending),
              delta="worker will enrich these", delta_color="off")
    st.markdown('</div>', unsafe_allow_html=True)

with k4:
    st.markdown('<div class="bucket-card bc-none">', unsafe_allow_html=True)
    st.metric("❌  No contact info",  len(df_none))
    st.markdown('</div>', unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# ── Four tabs ───────────────────────────────────────────────
tab_call, tab_email, tab_pending, tab_none = st.tabs([
    f"📞  Cold Calling  ({len(df_call)})",
    f"📧  Email Outreach  ({len(df_email)})",
    f"🔍  Pending Extraction  ({len(df_pending)})",
    f"❌  No Contact  ({len(df_none)})",
])

# ── Shared column sets ──────────────────────────────────────
CALL_COLS  = ["id", "business_name", "phone", "email", "city", "country",
              "website", "rating", "reviews", "status"]
EMAIL_COLS = ["id", "business_name", "email", "phone", "domain",
              "city", "country", "status"]
PEND_COLS  = ["id", "business_name", "domain", "phone",
              "city", "country", "status", "created_at"]
NONE_COLS  = ["id", "business_name", "website", "address",
              "city", "country", "source", "created_at"]


def _safe_cols(df, cols):
    return [c for c in cols if c in df.columns]


def _search_filter(df, key):
    q = st.text_input("Search", placeholder="Filter by name, city, domain…", key=key)
    if q:
        mask = df.apply(lambda row: row.astype(str).str.contains(q, case=False).any(), axis=1)
        return df[mask]
    return df


# ── Tab 1 — Cold Calling ────────────────────────────────────
with tab_call:
    if df_call.empty:
        st.info("No leads with phone numbers yet. Run a Google Maps scrape — it pulls phone numbers automatically.")
    else:
        st.caption(
            f"**{len(df_call)}** leads have a phone number and are ready for cold outreach. "
            f"{int(has_val(df_call['email']).sum())} of them also have an email."
        )
        filtered = _search_filter(df_call, "search_call")

        # Status filter
        statuses = ["All"] + sorted(df_call["status"].dropna().unique().tolist())
        sel = st.selectbox("Status", statuses, key="status_call")
        if sel != "All":
            filtered = filtered[filtered["status"] == sel]

        st.dataframe(
            filtered[_safe_cols(filtered, CALL_COLS)],
            use_container_width=True, hide_index=True, height=420,
        )
        st.download_button(
            "📥  Download calling list (CSV)",
            filtered[_safe_cols(filtered, CALL_COLS)].to_csv(index=False).encode("utf-8"),
            file_name="leads_calling.csv", mime="text/csv",
            key="dl_call",
        )

# ── Tab 2 — Email Outreach ──────────────────────────────────
with tab_email:
    if df_email.empty:
        st.info("No leads with emails yet. Run the Email Extractor or let the worker enrich them automatically.")
    else:
        st.caption(
            f"**{len(df_email)}** leads have an email and are ready for outreach. "
            f"Head to the **Outreach** page to add them to a campaign."
        )
        filtered = _search_filter(df_email, "search_email")

        statuses = ["All"] + sorted(df_email["status"].dropna().unique().tolist())
        sel = st.selectbox("Status", statuses, key="status_email")
        if sel != "All":
            filtered = filtered[filtered["status"] == sel]

        st.dataframe(
            filtered[_safe_cols(filtered, EMAIL_COLS)],
            use_container_width=True, hide_index=True, height=420,
        )
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                "📥  Download email list (CSV)",
                filtered[_safe_cols(filtered, EMAIL_COLS)].to_csv(index=False).encode("utf-8"),
                file_name="leads_email.csv", mime="text/csv",
                key="dl_email",
            )
        with col_dl2:
            # Plain email-per-line for mail merge tools
            email_list = "\n".join(filtered["email"].dropna().unique().tolist())
            st.download_button(
                "📋  Download email addresses only (.txt)",
                email_list.encode("utf-8"),
                file_name="emails_only.txt", mime="text/plain",
                key="dl_emails_txt",
            )

# ── Tab 3 — Pending Extraction ──────────────────────────────
with tab_pending:
    if df_pending.empty:
        st.success("All leads with domains have been enriched.")
    else:
        st.caption(
            f"**{len(df_pending)}** leads have a domain but no email yet. "
            f"The worker extracts these automatically every 30 s — or paste the domains below into the **Email Extractor**."
        )
        filtered = _search_filter(df_pending, "search_pending")

        st.dataframe(
            filtered[_safe_cols(filtered, PEND_COLS)],
            use_container_width=True, hide_index=True, height=380,
        )

        # Domain list for manual copy-paste into Email Extractor
        domains = sorted(df_pending["domain"].dropna().unique().tolist())
        with st.expander(f"📋  Copy {len(domains)} domain(s) → paste into Email Extractor"):
            st.code("\n".join(domains), language="text")

        st.download_button(
            "📥  Download pending leads (CSV)",
            filtered[_safe_cols(filtered, PEND_COLS)].to_csv(index=False).encode("utf-8"),
            file_name="leads_pending_extraction.csv", mime="text/csv",
            key="dl_pending",
        )

# ── Tab 4 — No Contact ──────────────────────────────────────
with tab_none:
    if df_none.empty:
        st.success("Every lead has at least one contact channel.")
    else:
        st.caption(
            f"**{len(df_none)}** leads have no phone, no email, and no domain. "
            "These may be incomplete scrape results — check the website column manually."
        )
        filtered = _search_filter(df_none, "search_none")

        st.dataframe(
            filtered[_safe_cols(filtered, NONE_COLS)],
            use_container_width=True, hide_index=True, height=360,
        )
        st.download_button(
            "📥  Download (CSV)",
            filtered[_safe_cols(filtered, NONE_COLS)].to_csv(index=False).encode("utf-8"),
            file_name="leads_no_contact.csv", mime="text/csv",
            key="dl_none",
        )

st.divider()

# ══════════════════════════════════════════════════════════════
# CHARTS
# ══════════════════════════════════════════════════════════════
chart_l, chart_r = st.columns(2)

with chart_l:
    st.subheader("Leads by status")
    status_counts = db.leads_by_status()
    if status_counts:
        st.bar_chart(
            pd.DataFrame([{"status": k, "count": v}
                          for k, v in status_counts.items()]).set_index("status"),
            height=240,
        )
    else:
        st.caption("_No data yet._")

with chart_r:
    st.subheader("Email confidence breakdown")
    with db.get_conn() as c:
        conf_rows = c.execute(
            "SELECT confidence, COUNT(*) AS n FROM emails GROUP BY confidence"
        ).fetchall()
    if conf_rows:
        st.bar_chart(
            pd.DataFrame([dict(r) for r in conf_rows]).set_index("confidence"),
            height=240,
        )
    else:
        st.caption("_No emails yet._")

# ── Extraction trend ────────────────────────────────────────
st.subheader("Emails extracted — last 14 days")
trend = db.emails_by_day(days=14)
if trend:
    st.bar_chart(pd.DataFrame(trend).set_index("day"), height=220)
else:
    st.caption("_No data yet._")

st.divider()

# ── Top domains ─────────────────────────────────────────────
st.subheader("Top domains by email count")
top = db.emails_by_domain(limit=25)
if top:
    st.dataframe(pd.DataFrame(top), use_container_width=True, hide_index=True)
else:
    st.caption("_No domains yet._")

st.divider()

# ── Run history ─────────────────────────────────────────────
st.subheader("Run history")
runs = db.runs_summary(limit=50)
if runs:
    st.dataframe(pd.DataFrame(runs), use_container_width=True, hide_index=True)
else:
    st.caption("_No runs yet._")
