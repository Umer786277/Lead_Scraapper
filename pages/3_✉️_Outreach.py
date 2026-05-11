"""
Outreach — templates, campaigns, send queue.

Needs SMTP credentials (Gmail App Password, Outlook, SES SMTP, etc.).
Set SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASSWORD env vars, or override
in the sidebar.
"""

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

import db
from outreach import SMTPConfig, process_queue, render, seed_default_templates, send_email

st.set_page_config(page_title="Outreach", page_icon="✉️", layout="wide")
db.init_db()

# ── Styling ─────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; max-width: 1200px; }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #1a1d2e 0%, #111321 100%); }

    .hero {
        background: linear-gradient(135deg, #ec4899 0%, #8b5cf6 100%);
        border-radius: 18px;
        padding: 1.75rem 2.25rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 10px 40px rgba(236,72,153,0.2);
    }
    .hero h1 { color: #fff; margin: 0; font-size: 1.9rem; }
    .hero p  { color: rgba(255,255,255,0.85); margin: 0.4rem 0 0 0; }

    .pill {
        display: inline-block; padding: 0.2rem 0.65rem;
        border-radius: 999px; font-size: 0.78rem; font-weight: 600;
    }
    .pill-ok   { background: rgba(34,197,94,0.18);  color: #4ade80; }
    .pill-warn { background: rgba(234,179,8,0.18);  color: #facc15; }
    .pill-err  { background: rgba(239,68,68,0.18);  color: #f87171; }
    .pill-mute { background: rgba(148,163,184,0.15); color: #cbd5e1; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <h1>✉️  Outreach</h1>
    <p>Templates, campaigns, and a queue worker for automated follow-ups. Set SMTP in the sidebar.</p>
</div>
""", unsafe_allow_html=True)

# ── Sidebar: SMTP config ────────────────────────────────────
with st.sidebar:
    st.markdown("### 📤  SMTP settings")
    env = SMTPConfig.from_env()

    smtp_host = st.text_input("Host", env.host or "smtp.gmail.com")
    col_p, col_t = st.columns([2, 1])
    with col_p:
        smtp_port = st.number_input("Port", 1, 65535, env.port or 587)
    with col_t:
        smtp_tls = st.selectbox("Encryption", ["STARTTLS", "SSL"],
                                index=0 if env.use_tls else 1)
    smtp_user = st.text_input("Username / from-address", env.username or "")
    smtp_pass = st.text_input("Password / App Password", env.password or "", type="password")
    from_addr = st.text_input("Visible 'From' address", smtp_user or "")
    reply_to  = st.text_input("Reply-To (optional)", "")

    smtp = SMTPConfig(
        host=smtp_host, port=int(smtp_port),
        username=smtp_user, password=smtp_pass,
        use_tls=(smtp_tls == "STARTTLS"),
    )
    has_smtp = bool(smtp.host and smtp.username and smtp.password and from_addr)

    st.caption("💡  Gmail requires a 16-char App Password (not your real password).")

    if st.button("📧  Send test to self", disabled=not has_smtp, use_container_width=True):
        try:
            send_email(smtp, to=smtp_user, subject="Outreach test",
                       body="SMTP wiring works ✓", from_addr=from_addr, reply_to=reply_to or None)
            st.success("✅  Sent. Check your inbox.")
        except Exception as e:
            st.error(f"❌  {e}")

# Seed default templates the first time
if not db.list_templates():
    seed_default_templates()

tpl_tab, camp_tab, queue_tab, leads_tab = st.tabs(
    ["📝  Templates", "🎯  Campaigns", "📨  Queue", "👥  Leads"]
)

# ══════════════════════════════════════════════════════════════
# TEMPLATES
# ══════════════════════════════════════════════════════════════
with tpl_tab:
    templates = db.list_templates()

    col_list, col_edit = st.columns([1, 2])
    with col_list:
        st.markdown("#### Saved templates")
        if not templates:
            st.caption("_No templates yet._")
        for t in templates:
            with st.container(border=True):
                st.markdown(f"**{t['name']}**")
                st.caption(f"Updated {t['updated_at']}")

        st.divider()
        if st.button("+ New template", use_container_width=True):
            st.session_state.editing_template = {
                "id": None, "name": "", "subject": "", "body": ""
            }

    with col_edit:
        st.markdown("#### Edit template")
        if "editing_template" not in st.session_state and templates:
            st.session_state.editing_template = templates[0]

        picked_id = st.selectbox(
            "Load template",
            options=[t["id"] for t in templates] + [0],
            format_func=lambda tid: next((t["name"] for t in templates if t["id"] == tid), "➕  New"),
            key="template_picker",
        )
        if picked_id:
            t = db.get_template(picked_id)
            if t and st.session_state.get("editing_template", {}).get("id") != picked_id:
                st.session_state.editing_template = t
        elif picked_id == 0:
            st.session_state.editing_template = {
                "id": None, "name": "", "subject": "", "body": ""
            }

        et = st.session_state.get("editing_template", {"id": None, "name": "", "subject": "", "body": ""})

        name = st.text_input("Template name", et.get("name", ""), key="tpl_name")
        subject = st.text_input("Subject", et.get("subject", ""), key="tpl_subject")
        body = st.text_area("Body", et.get("body", ""), height=280, key="tpl_body",
                            help="Supports placeholders: {{first_name}}, {{business_name}}, {{domain}}, {{city}}, etc.")

        col_save, col_preview, col_delete = st.columns([1, 1, 1])
        with col_save:
            if st.button("💾  Save", type="primary", use_container_width=True,
                         disabled=not (name and subject and body)):
                new_id = db.upsert_template(name, subject, body, template_id=et.get("id"))
                st.session_state.editing_template = db.get_template(new_id)
                st.success(f"Saved template #{new_id}")
                st.rerun()
        with col_preview:
            if st.button("👁  Preview", use_container_width=True):
                st.session_state.preview_open = True
        with col_delete:
            if et.get("id") and st.button("🗑  Delete", use_container_width=True):
                db.delete_template(et["id"])
                st.session_state.editing_template = {"id": None, "name": "", "subject": "", "body": ""}
                st.rerun()

        if st.session_state.get("preview_open"):
            sample_lead = {
                "first_name": "Sarah",
                "business_name": "Animal Clinic Ltd",
                "domain": "animalclinic.co.uk",
                "city": "London",
                "country": "UK",
                "email": "sarah@animalclinic.co.uk",
            }
            with st.container(border=True):
                st.caption("Rendered against sample lead:")
                st.markdown(f"**Subject:** {render(subject, sample_lead)}")
                st.code(render(body, sample_lead), language="text")

# ══════════════════════════════════════════════════════════════
# CAMPAIGNS
# ══════════════════════════════════════════════════════════════
with camp_tab:
    st.markdown("#### Create a campaign")

    templates = db.list_templates()
    if len(templates) < 1:
        st.warning("Create at least one template before starting a campaign.")
    else:
        camp_name = st.text_input("Campaign name", "Spring outreach — vet clinics")

        st.markdown("##### Sequence")
        st.caption("Each step fires after the cumulative delay. Step 1 should be delay 0.")

        default_sequence = [
            (0, "Step 1 — Intro"),
            (3, "Step 2 — Bump"),
            (7, "Step 3 — Value"),
            (7, "Step 4 — Break-up"),
        ]

        steps_data = []
        name_to_id = {t["name"]: t["id"] for t in templates}
        for i, (default_delay, default_name) in enumerate(default_sequence, 1):
            cols = st.columns([1, 3, 1])
            with cols[0]:
                delay = st.number_input(f"Step {i} — delay (days)", 0, 60, default_delay, key=f"delay_{i}")
            with cols[1]:
                tpl_id = st.selectbox(
                    f"Step {i} — template",
                    options=[t["id"] for t in templates],
                    index=([t["name"] for t in templates].index(default_name)
                           if default_name in name_to_id else 0),
                    format_func=lambda tid: next(t["name"] for t in templates if t["id"] == tid),
                    key=f"tpl_{i}",
                )
            with cols[2]:
                enabled = st.checkbox(f"Enable #{i}", value=(i <= 4), key=f"en_{i}")
            if enabled:
                steps_data.append({"delay_days": int(delay), "template_id": int(tpl_id)})

        st.divider()
        st.markdown("##### Enrol leads")
        source_filter = st.selectbox("Source", ["Any", "google_maps", "email_extractor"], index=0)
        status_filter = st.selectbox("Lead status", ["new", "contacted", "replied"], index=0,
                                     help="'new' = never contacted yet")

        pool = db.sendable_leads(
            source=None if source_filter == "Any" else source_filter,
            status=status_filter,
            limit=1000,
        )
        if not pool:
            st.info(f"No leads match those filters. (Need leads with an email and status='{status_filter}'.)")
            pool_df = pd.DataFrame()
        else:
            pool_df = pd.DataFrame(pool)
            st.caption(f"{len(pool_df)} matching leads")
            st.dataframe(
                pool_df[["id", "source", "business_name", "domain", "email", "status"]],
                use_container_width=True, hide_index=True, height=260,
            )

        selected_ids = st.multiselect(
            "Pick leads to enrol",
            options=pool_df["id"].tolist() if not pool_df.empty else [],
            default=pool_df["id"].tolist() if not pool_df.empty else [],
            format_func=lambda i: next(
                (f"#{row['id']} — {row['email']} ({row['business_name'] or row['domain']})"
                 for row in pool if row["id"] == i), str(i)),
        )

        disabled_reason = (
            "No leads selected" if not selected_ids else
            "No enabled steps" if not steps_data else
            "Campaign name required" if not camp_name.strip() else None
        )

        if st.button(
            f"🎯  Create campaign ({len(selected_ids)} leads × {len(steps_data)} steps)",
            type="primary",
            disabled=disabled_reason is not None,
            use_container_width=False,
        ):
            cid = db.create_campaign(camp_name, steps_data, selected_ids)
            st.success(f"✅  Campaign #{cid} created — {len(selected_ids) * len(steps_data)} sends scheduled.")
            st.rerun()
        elif disabled_reason:
            st.caption(f"_{disabled_reason}._")

    st.divider()
    st.markdown("#### Existing campaigns")
    camps = db.list_campaigns()
    if not camps:
        st.caption("_No campaigns yet._")
    else:
        for c in camps:
            with st.container(border=True):
                cols = st.columns([3, 1, 1, 1, 1])
                status_pill = {
                    "active":  "<span class='pill pill-ok'>active</span>",
                    "paused":  "<span class='pill pill-warn'>paused</span>",
                    "done":    "<span class='pill pill-mute'>done</span>",
                }.get(c["status"], c["status"])
                cols[0].markdown(f"**#{c['id']} {c['name']}**<br>{status_pill}  · {c['leads_count']} leads", unsafe_allow_html=True)
                cols[1].metric("Sent", c["sent_count"])
                cols[2].metric("Pending", c["pending_count"])
                with cols[3]:
                    if c["status"] == "active" and st.button("⏸  Pause", key=f"pause_{c['id']}", use_container_width=True):
                        db.set_campaign_status(c["id"], "paused")
                        st.rerun()
                    elif c["status"] == "paused" and st.button("▶  Resume", key=f"resume_{c['id']}", use_container_width=True):
                        db.set_campaign_status(c["id"], "active")
                        st.rerun()
                with cols[4]:
                    if st.button("✓  Done", key=f"done_{c['id']}", use_container_width=True):
                        db.set_campaign_status(c["id"], "done")
                        st.rerun()

# ══════════════════════════════════════════════════════════════
# QUEUE
# ══════════════════════════════════════════════════════════════
with queue_tab:
    summary = db.send_queue_summary()
    cols = st.columns(5)
    cols[0].metric("Pending",  summary.get("pending", 0))
    cols[1].metric("Sent",     summary.get("sent", 0))
    cols[2].metric("Skipped",  summary.get("skipped", 0))
    cols[3].metric("Failed",   summary.get("failed", 0))
    cols[4].metric("Cancelled", summary.get("cancelled", 0))

    st.markdown("#### Due now")
    due = db.due_sends(limit=100)
    if not due:
        st.info("Nothing due at the moment. Either the queue is empty or all scheduled times are in the future.")
    else:
        due_df = pd.DataFrame(due)
        st.dataframe(
            due_df[["send_id", "campaign_name", "step_number", "email", "scheduled_at", "template_name", "lead_status"]],
            use_container_width=True, hide_index=True, height=300,
        )

    col_run, col_dry, col_limit = st.columns([1, 1, 1])
    with col_limit:
        batch = st.number_input("Batch size", 1, 500, 25)
    with col_dry:
        dry = st.button("🧪  Dry-run (mark skipped)", use_container_width=True, disabled=not due)
    with col_run:
        run = st.button("🚀  Process queue", type="primary",
                        use_container_width=True, disabled=not (due and has_smtp))

    if dry or run:
        progress = st.progress(0.0, text="Processing...")
        log_box = st.empty()
        lines = []
        processed = [0]

        def _on(item, kind, info):
            processed[0] += 1
            pill = {
                "sent":    "✅",
                "skipped": "⏭",
                "failed":  "❌",
                "dry_run": "🧪",
            }.get(kind, "•")
            lines.append(f"{pill}  #{item['send_id']} → {item['email']}  ·  {info}")
            progress.progress(min(processed[0] / len(due), 1.0), text=f"[{processed[0]}/{len(due)}]")
            log_box.code("\n".join(lines[-20:]), language="text")

        result = process_queue(
            smtp=smtp, from_addr=from_addr, limit=int(batch),
            dry_run=dry, on_item=_on, reply_to=reply_to or None,
        )
        progress.empty()
        st.success(
            f"Done. Sent {result['sent']}, skipped {result['skipped']}, failed {result['failed']}."
        )
        st.rerun()

    if not has_smtp:
        st.warning("⚠️  Fill in SMTP settings (sidebar) before processing the queue.")

# ══════════════════════════════════════════════════════════════
# LEADS (manual status + events)
# ══════════════════════════════════════════════════════════════
with leads_tab:
    st.markdown("#### Lead pipeline")

    f1, f2, f3 = st.columns([1, 1, 2])
    with f1:
        only_with_email = st.toggle(
            "Email only", value=False,
            help="Off = show all leads (incl. Google Maps leads with only phone/website).",
        )
    with f2:
        source_filter = st.selectbox("Source", ["All", "google_maps", "email_extractor"], index=0)

    query = (
        "SELECT id, source, business_name, domain, email, phone, website, "
        "status, created_at "
        "FROM leads WHERE 1=1"
    )
    params = []
    if only_with_email:
        query += " AND email IS NOT NULL AND email != ''"
    if source_filter != "All":
        query += " AND source=%s"
        params.append(source_filter)
    query += " ORDER BY id DESC LIMIT 300"

    with db.get_conn() as c:
        rows = c.execute(query, tuple(params)).fetchall()
    leads = [dict(r) for r in rows]

    if not leads:
        st.caption("_No leads match those filters._")
    else:
        df = pd.DataFrame(leads)
        st.dataframe(df, use_container_width=True, hide_index=True, height=360)

        st.divider()
        st.markdown("#### Update a lead")
        def _lead_label(i):
            r = next((x for x in leads if x["id"] == i), None)
            if not r:
                return str(i)
            ident = r.get("email") or r.get("phone") or r.get("domain") or r.get("business_name") or "(no contact)"
            return f"#{r['id']} — {ident}"

        lead_id = st.selectbox(
            "Lead",
            options=[r["id"] for r in leads],
            format_func=_lead_label,
        )
        new_status = st.selectbox(
            "Mark as", ["replied", "converted", "dead", "contacted", "new"], index=0,
            help="Marking a lead as 'replied'/'converted'/'dead' cancels any pending follow-ups.",
        )
        note = st.text_input("Note (optional)", "")

        if st.button("Update"):
            db.update_lead_status(lead_id, new_status, note=note or None)
            if new_status in ("replied", "converted", "dead"):
                db.cancel_future_sends_for_lead(lead_id)
            db.log_event(lead_id=lead_id, event_type="manual_note",
                         note=f"status → {new_status}" + (f" · {note}" if note else ""))
            st.success(f"Lead #{lead_id} → {new_status}. Future sends cancelled where applicable.")
            st.rerun()

        st.divider()
        st.markdown("#### Event log for selected lead")
        events = db.events_for_lead(lead_id)
        if events:
            st.dataframe(pd.DataFrame(events), use_container_width=True, hide_index=True)
        else:
            st.caption("_No events yet._")
