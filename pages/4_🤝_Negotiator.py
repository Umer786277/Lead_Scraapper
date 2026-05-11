"""
Negotiator — human-in-the-loop vendor negotiation.

Flow:
1. IMAP tab → poll inbox, match replies to sent emails, create threads
2. Threads tab → pick a thread, set price floor + goal
3. Generate draft → OpenAI produces structured JSON (draft + analysis)
4. Review → edit → Approve & send (via SMTP) or Reject
5. Deals tab → manually log closed deals + CSV export
"""

import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db
from inbox import IMAPConfig, check_inbox
from llm import DEFAULT_MODEL, generate_negotiation_draft
from outreach import SMTPConfig, send_email

st.set_page_config(page_title="Negotiator", page_icon="🤝", layout="wide")
db.init_db()

# ── Styling ─────────────────────────────────────────────────
st.markdown("""
<style>
    .block-container { padding-top: 1.5rem; max-width: 1200px; }
    [data-testid="stSidebar"] { background: linear-gradient(180deg, #1a1d2e 0%, #111321 100%); }

    .hero {
        background: linear-gradient(135deg, #10b981 0%, #0ea5e9 100%);
        border-radius: 18px;
        padding: 1.75rem 2.25rem;
        margin-bottom: 1.5rem;
        box-shadow: 0 10px 40px rgba(14,165,233,0.2);
    }
    .hero h1 { color: #fff; margin: 0; font-size: 1.9rem; }
    .hero p  { color: rgba(255,255,255,0.85); margin: 0.4rem 0 0 0; }

    .pill {
        display: inline-block; padding: 0.2rem 0.65rem;
        border-radius: 999px; font-size: 0.78rem; font-weight: 600;
    }
    .pill-active { background: rgba(34,197,94,0.18);  color: #4ade80; }
    .pill-won    { background: rgba(99,102,241,0.18); color: #a5b4fc; }
    .pill-lost   { background: rgba(239,68,68,0.18);  color: #f87171; }
    .pill-stall  { background: rgba(148,163,184,0.15); color: #cbd5e1; }

    .msg-us        { background: rgba(99,102,241,0.08);
                     border-left: 3px solid #818cf8;
                     padding: 0.75rem 1rem; border-radius: 8px; margin: 0.5rem 0; }
    .msg-vendor    { background: rgba(236,72,153,0.08);
                     border-left: 3px solid #f472b6;
                     padding: 0.75rem 1rem; border-radius: 8px; margin: 0.5rem 0; }
    .msg-meta      { font-size: 0.78rem; opacity: 0.7; margin-bottom: 0.35rem; }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <h1>🤝  Negotiator</h1>
    <p>AI-drafted, human-approved vendor negotiations. OpenAI generates, you approve before any email leaves.</p>
</div>
""", unsafe_allow_html=True)

# ── Sidebar: env status + SMTP ─────────────────────────────
with st.sidebar:
    st.markdown("### 🔑  Environment")
    has_openai = bool(os.getenv("OPENAI_API_KEY"))
    st.markdown(
        f"OpenAI: {'✅ key loaded' if has_openai else '❌ missing `OPENAI_API_KEY`'}"
    )
    model_id = st.text_input("Model", DEFAULT_MODEL, help="Any OpenAI chat model.")

    st.divider()
    st.markdown("### 📤  SMTP (for sending approvals)")
    env_smtp = SMTPConfig.from_env()
    smtp_host = st.text_input("Host", env_smtp.host or "smtp.gmail.com")
    smtp_port = st.number_input("Port", 1, 65535, env_smtp.port or 587)
    smtp_tls  = st.selectbox("Encryption", ["STARTTLS", "SSL"], index=0 if env_smtp.use_tls else 1)
    smtp_user = st.text_input("Username", env_smtp.username or "")
    smtp_pass = st.text_input("Password", env_smtp.password or "", type="password")
    from_addr = st.text_input("From address", smtp_user or "")

    smtp = SMTPConfig(
        host=smtp_host, port=int(smtp_port),
        username=smtp_user, password=smtp_pass,
        use_tls=(smtp_tls == "STARTTLS"),
    )
    smtp_ready = bool(smtp.host and smtp.username and smtp.password and from_addr)

    st.divider()
    st.markdown("### 📨  IMAP (for checking replies)")
    env_imap = IMAPConfig.from_env()
    imap_host = st.text_input("IMAP host", env_imap.host or "imap.gmail.com")
    imap_port = st.number_input("IMAP port", 1, 65535, env_imap.port or 993)
    imap_user = st.text_input("IMAP user", env_imap.username or smtp_user or "")
    imap_pass = st.text_input("IMAP password", env_imap.password or "", type="password")
    imap_cfg = IMAPConfig(
        host=imap_host, port=int(imap_port),
        username=imap_user, password=imap_pass,
        mailbox=env_imap.mailbox,
    )

inbox_tab, threads_tab, deals_tab = st.tabs(
    ["📨  Inbox", "💬  Threads", "🏆  Deals"]
)

# ══════════════════════════════════════════════════════════════
# INBOX
# ══════════════════════════════════════════════════════════════
with inbox_tab:
    st.markdown("#### Poll inbox for vendor replies")
    st.caption(
        "Scans UNSEEN messages, matches each against emails we sent (via Message-ID), "
        "and threads matching replies. Gmail: enable IMAP + use an App Password."
    )

    col_a, col_b = st.columns([1, 2])
    with col_a:
        limit = st.number_input("Max messages per scan", 5, 500, 50)
    with col_b:
        pass

    scan_clicked = st.button(
        "🔍  Check inbox now",
        type="primary",
        disabled=not imap_cfg.ready,
        use_container_width=False,
    )
    if not imap_cfg.ready:
        st.caption("_Fill in IMAP credentials in the sidebar to enable._")

    if scan_clicked:
        progress = st.progress(0.0, text="Connecting...")

        def _on(i, total):
            progress.progress(min(i / max(total, 1), 1.0), text=f"[{i}/{total}] parsing...")

        try:
            matched = check_inbox(imap_cfg, limit=int(limit), on_progress=_on)
            progress.empty()
            if matched:
                st.success(f"✅  Matched {len(matched)} reply/replies to our sends.")
                st.dataframe(pd.DataFrame(matched), use_container_width=True, hide_index=True)
            else:
                st.info("No new replies matched emails we sent.")
        except Exception as e:
            progress.empty()
            st.error(f"❌  {e}")

    st.divider()
    st.markdown("#### ✍️  Add a reply manually (testing)")
    st.caption(
        "Use this to test the negotiation flow without IMAP — paste a vendor reply "
        "and attach it to an existing lead."
    )

    with db.get_conn() as c:
        lead_options = [
            dict(r) for r in c.execute(
                "SELECT id, business_name, email, domain FROM leads "
                "WHERE email IS NOT NULL AND email != '' ORDER BY id DESC LIMIT 200"
            ).fetchall()
        ]

    if not lead_options:
        st.caption("_No leads with emails yet._")
    else:
        lead_pick = st.selectbox(
            "Lead",
            [l["id"] for l in lead_options],
            format_func=lambda i: next(
                (f"#{l['id']} — {l['email']} ({l['business_name'] or l['domain']})"
                 for l in lead_options if l["id"] == i), str(i)),
        )
        manual_subject = st.text_input("Reply subject", "Re: Quick thought for Your Clinic")
        manual_body = st.text_area("Reply body", height=180,
            value="Hi,\n\nThanks for reaching out. Our starting price is $620/month.\n\nBest,\nVendor")

        if st.button("Add as inbound message"):
            tid = db.get_or_create_thread(lead_pick, subject=manual_subject)
            db.insert_thread_message(
                thread_id=tid, direction="inbound",
                subject=manual_subject, body=manual_body,
                from_addr=next((l["email"] for l in lead_options if l["id"] == lead_pick), ""),
            )
            db.update_lead_status(lead_pick, "replied")
            st.success(f"Added. Thread #{tid} updated.")
            st.rerun()

# ══════════════════════════════════════════════════════════════
# THREADS
# ══════════════════════════════════════════════════════════════
with threads_tab:
    threads = db.list_threads()
    if not threads:
        st.info("No vendor threads yet. Check the inbox or add a reply manually.")
    else:
        col_list, col_detail = st.columns([1, 2])

        with col_list:
            st.markdown("#### Active threads")
            labels = [
                f"#{t['id']} · {t['business_name'] or t['domain'] or t['email']} "
                f"· {t['inbound_count']} reply"
                for t in threads
            ]
            pick_idx = st.radio(
                "Pick a thread",
                options=range(len(threads)),
                format_func=lambda i: labels[i],
                label_visibility="collapsed",
            )
            t = threads[pick_idx]

        with col_detail:
            status_class = {
                "active":  "pill-active", "won":     "pill-won",
                "lost":    "pill-lost",   "stalled": "pill-stall",
            }.get(t["status"], "pill-stall")

            st.markdown(
                f"### {t['business_name'] or t['domain'] or t['email']}  "
                f"<span class='pill {status_class}'>{t['status']}</span>",
                unsafe_allow_html=True,
            )
            st.caption(
                f"📧 {t['email']}  ·  thread #{t['id']}  ·  "
                f"{t['total_msgs']} message(s)"
            )

            # ── Thread settings ───────────────────────────────
            with st.expander("⚙️  Negotiation settings", expanded=(t["price_floor"] is None)):
                sc1, sc2, sc3 = st.columns([1, 1, 2])
                with sc1:
                    floor = st.number_input(
                        "Price floor",
                        min_value=0.0, value=float(t["price_floor"] or 0.0), step=10.0,
                        help="Minimum price you'd accept. Never revealed to the vendor.",
                    )
                with sc2:
                    curr = st.text_input("Currency", t["currency"] or "USD")
                with sc3:
                    goal = st.text_input(
                        "Negotiation goal (optional)",
                        t["goal"] or "",
                        placeholder="e.g. Below $400/month with 12-month commitment",
                    )
                if st.button("Save settings", key=f"save_settings_{t['id']}"):
                    db.update_thread(
                        t["id"],
                        price_floor=floor if floor > 0 else None,
                        currency=curr,
                        goal=goal or None,
                    )
                    st.rerun()

            # ── Message history ───────────────────────────────
            msgs = db.thread_messages(t["id"])
            if not msgs:
                st.caption("_No messages yet._")
            else:
                st.markdown("#### Conversation")
                for m in msgs:
                    cls = "msg-us" if m["direction"] == "outbound" else "msg-vendor"
                    who = "US" if m["direction"] == "outbound" else "VENDOR"
                    body_html = (m["body"] or "").replace("\n", "<br>")
                    st.markdown(
                        f"<div class='{cls}'>"
                        f"<div class='msg-meta'>{who}  ·  {m['created_at']}  ·  "
                        f"<b>{m['subject'] or ''}</b></div>"
                        f"{body_html}</div>",
                        unsafe_allow_html=True,
                    )

            # ── Draft area ────────────────────────────────────
            st.divider()
            st.markdown("#### 🤖  Draft a reply")

            pending = db.pending_draft_for_thread(t["id"])

            col_gen, col_close = st.columns([1, 1])
            with col_gen:
                gen_btn = st.button(
                    "✨  Generate draft with OpenAI",
                    type="primary" if not pending else "secondary",
                    disabled=not has_openai or not msgs,
                    use_container_width=True,
                    key=f"gen_{t['id']}",
                )
            with col_close:
                close_btn = st.button(
                    "🏁  Log as closed deal",
                    use_container_width=True,
                    key=f"close_{t['id']}",
                )

            if gen_btn:
                with st.spinner("Thinking..."):
                    history = [
                        {"direction": m["direction"], "subject": m["subject"],
                         "body": m["body"] or ""}
                        for m in msgs
                    ]
                    try:
                        draft = generate_negotiation_draft(
                            history,
                            price_floor=t["price_floor"],
                            currency=t["currency"] or "USD",
                            goal=t["goal"],
                            our_company=os.getenv("OUR_COMPANY_NAME", "our team"),
                            model=model_id,
                        )
                        db.insert_draft(
                            thread_id=t["id"],
                            model=draft.get("model"),
                            draft_subject=draft.get("draft_subject", ""),
                            draft_body=draft.get("draft_body", ""),
                            detected_price=draft.get("detected_price"),
                            detected_currency=draft.get("detected_currency"),
                            reasoning=draft.get("reasoning", ""),
                            suggested_action=draft.get("suggested_action"),
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌  {e}")

            # Re-fetch after potential insert
            pending = db.pending_draft_for_thread(t["id"])
            if pending:
                st.markdown("##### Pending draft")
                ac1, ac2, ac3 = st.columns(3)
                ac1.metric("Detected price",
                           f"{pending['detected_price']:.2f}" if pending["detected_price"] else "—")
                ac2.metric("Currency", pending["detected_currency"] or "—")
                ac3.metric("Suggested action", pending["suggested_action"] or "—")

                if pending["reasoning"]:
                    st.caption(f"💭  {pending['reasoning']}")

                new_subj = st.text_input("Subject", pending["draft_subject"] or "",
                                         key=f"ds_{pending['id']}")
                new_body = st.text_area("Body", pending["draft_body"] or "", height=240,
                                         key=f"db_{pending['id']}")

                b1, b2, b3 = st.columns(3)
                with b1:
                    send_clicked = st.button(
                        "✅  Approve & send",
                        type="primary",
                        disabled=not smtp_ready,
                        use_container_width=True,
                        key=f"send_{pending['id']}",
                    )
                with b2:
                    reject_clicked = st.button(
                        "❌  Reject draft",
                        use_container_width=True,
                        key=f"rej_{pending['id']}",
                    )
                with b3:
                    regen_clicked = st.button(
                        "🔁  Regenerate",
                        use_container_width=True,
                        key=f"regen_{pending['id']}",
                    )

                if send_clicked:
                    # Persist any user edits first
                    db.update_draft(
                        pending["id"],
                        draft_subject=new_subj,
                        draft_body=new_body,
                    )
                    try:
                        msg_id = send_email(
                            smtp=smtp,
                            to=t["email"],
                            subject=new_subj,
                            body=new_body,
                            from_addr=from_addr,
                        )
                        db.insert_thread_message(
                            thread_id=t["id"],
                            direction="outbound",
                            subject=new_subj, body=new_body,
                            message_id=msg_id, from_addr=from_addr, to_addr=t["email"],
                        )
                        db.update_draft(pending["id"], status="sent")
                        db.log_event(
                            lead_id=t["lead_id"],
                            event_type="sent",
                            subject=new_subj,
                            message_id=msg_id,
                            note=f"negotiation reply (draft #{pending['id']})",
                        )
                        st.success("✅  Sent.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"❌  Send failed: {e}")

                if reject_clicked:
                    db.update_draft(pending["id"], status="rejected")
                    st.rerun()

                if regen_clicked:
                    db.update_draft(pending["id"], status="rejected")
                    st.rerun()

            # ── Close as deal ────────────────────────────────
            if close_btn:
                st.session_state[f"closing_{t['id']}"] = True

            if st.session_state.get(f"closing_{t['id']}"):
                with st.container(border=True):
                    st.markdown("##### Log final deal")
                    dc1, dc2 = st.columns([1, 1])
                    with dc1:
                        final_price = st.number_input("Final price", 0.0, 1e9, 0.0, step=10.0,
                                                      key=f"fp_{t['id']}")
                    with dc2:
                        final_curr = st.text_input("Currency", t["currency"] or "USD",
                                                   key=f"fc_{t['id']}")
                    terms = st.text_area("Terms", height=100, key=f"terms_{t['id']}",
                        placeholder="e.g. 12-month contract, net-30, includes support")
                    notes = st.text_area("Notes", height=70, key=f"dnotes_{t['id']}")

                    cfg_col1, cfg_col2 = st.columns(2)
                    with cfg_col1:
                        mark_outcome = st.selectbox("Thread outcome", ["won", "lost", "stalled"],
                                                    key=f"outcome_{t['id']}")
                    with cfg_col2:
                        if st.button("Save deal", type="primary", key=f"save_deal_{t['id']}"):
                            db.create_deal(
                                lead_id=t["lead_id"],
                                thread_id=t["id"],
                                vendor_name=t["business_name"] or t["domain"] or t["email"],
                                final_price=final_price,
                                currency=final_curr,
                                terms=terms,
                                notes=notes,
                            )
                            db.update_thread(t["id"], status=mark_outcome)
                            db.update_lead_status(
                                t["lead_id"],
                                "converted" if mark_outcome == "won" else "dead",
                            )
                            st.session_state[f"closing_{t['id']}"] = False
                            st.success("Deal logged.")
                            st.rerun()

# ══════════════════════════════════════════════════════════════
# DEALS
# ══════════════════════════════════════════════════════════════
with deals_tab:
    deals = db.list_deals()
    if not deals:
        st.info("No deals logged yet. Close one from the Threads tab.")
    else:
        df = pd.DataFrame(deals)
        total = df["final_price"].fillna(0).sum()
        c1, c2, c3 = st.columns(3)
        c1.metric("Closed deals", len(df))
        c2.metric("Total value", f"{total:,.2f}")
        c3.metric("Avg deal",    f"{(total / max(len(df), 1)):,.2f}")

        st.divider()
        st.dataframe(
            df[["id", "business_name", "vendor_email", "final_price",
                "currency", "terms", "closed_at"]],
            use_container_width=True, hide_index=True,
        )
        st.download_button(
            "📥  Export deals CSV",
            df.to_csv(index=False).encode("utf-8"),
            file_name="deals.csv",
            mime="text/csv",
        )
