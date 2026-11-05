"""Guards the single-organization Streamlit UI from cross-tenant database access."""

import streamlit as st

import db


def require_single_org():
    with db.get_conn() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM organizations WHERE status='active'"
        ).fetchone()["n"]
    if count > 1:
        st.error(
            "The legacy Streamlit UI is disabled for multi-organization databases. "
            "Use the authenticated Next.js application instead."
        )
        st.stop()
