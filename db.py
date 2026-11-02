"""
PostgreSQL storage layer (Supabase-compatible).

Set DATABASE_URL in .env. NEVER commit it — it contains credentials.

    DATABASE_URL=postgresql://user:password@host:5432/postgres

Uses psycopg v3 with dict-row factory. Compatible with Supabase's
transaction pooler (prepare_threshold=None disables prepared statements
which pgbouncer transaction mode doesn't support).
"""

import hashlib
import json
import math
import os
import time
from uuid import uuid4
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load_env_file(path):
    """Minimal .env loader with zero dependencies. Doesn't overwrite existing env vars.

    Handles: `KEY=value`, `KEY="value"`, `KEY='value'`, blank lines, # comments.
    Uses utf-8-sig so a leading BOM won't eat the first key.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(Path(__file__).resolve().parent / ".env")

import psycopg
from psycopg import errors as pg_errors
from psycopg import sql as psycopg_sql
from psycopg.rows import dict_row

DATABASE_URL = os.getenv("DATABASE_URL")

DEFAULT_ORG_ID = os.getenv("DEFAULT_ORG_ID", "00000000-0000-0000-0000-000000000001")

_ORG_DEFAULT = f"UUID NOT NULL DEFAULT '{DEFAULT_ORG_ID}'"


def org_id_or_default(org_id=None):
    return org_id or DEFAULT_ORG_ID


def _day_start_iso():
    return datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).replace(tzinfo=None).isoformat(timespec="seconds")


def _hour_start_iso():
    return datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    ).replace(tzinfo=None).isoformat(timespec="seconds")


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS organizations (
    id            UUID PRIMARY KEY,
    name          TEXT NOT NULL,
    slug          TEXT UNIQUE NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    plan          TEXT NOT NULL DEFAULT 'free',
    hourly_allocation_limit INTEGER NOT NULL DEFAULT 60
                  CHECK (hourly_allocation_limit BETWEEN 1 AND 3600),
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
    user_id       UUID NOT NULL,
    org_id        UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    role          TEXT NOT NULL DEFAULT 'user',
    email         TEXT,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (user_id, org_id)
);

CREATE TABLE IF NOT EXISTS platform_roles (
    user_id       UUID PRIMARY KEY,
    role          TEXT NOT NULL CHECK (role = 'super_admin'),
    created_at    TEXT NOT NULL DEFAULT now()::text
);

CREATE TABLE IF NOT EXISTS organization_invitations (
    id              UUID PRIMARY KEY,
    org_id          UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    invited_user_id UUID,
    email           TEXT NOT NULL,
    token_hash      TEXT,
    expires_at      TIMESTAMPTZ,
    role            TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')),
    invited_by      UUID NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'revoked')),
    created_at      TEXT NOT NULL,
    accepted_at     TEXT
);

CREATE TABLE IF NOT EXISTS deletion_jobs (
    id                  UUID PRIMARY KEY,
    kind                TEXT NOT NULL CHECK (kind IN ('organization', 'user')),
    target_id           UUID NOT NULL,
    target_label        TEXT NOT NULL,
    requested_by        UUID NOT NULL,
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    attempts            INTEGER NOT NULL DEFAULT 0,
    error               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    available_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_expires_at    TIMESTAMPTZ,
    locked_by           UUID,
    completed_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS coordination_leases (
    lease_key     TEXT PRIMARY KEY,
    owner_token   UUID NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS outreach_dispatch_state (
    org_id          UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE,
    next_send_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    paused_until    TIMESTAMPTZ,
    pause_reason    TEXT,
    allocation_hour TIMESTAMPTZ,
    last_slot_index INTEGER,
    allocated_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS extraction_runs (
    id            SERIAL PRIMARY KEY,
    org_id        {_ORG_DEFAULT},
    run_type      TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL,
    input_count   INTEGER DEFAULT 0,
    output_count  INTEGER DEFAULT 0,
    metadata      TEXT
);

CREATE TABLE IF NOT EXISTS domains (
    id             SERIAL PRIMARY KEY,
    org_id         {_ORG_DEFAULT},
    run_id         INTEGER REFERENCES extraction_runs(id),
    domain         TEXT NOT NULL,
    status         TEXT NOT NULL,
    has_mx         INTEGER,
    pages_visited  INTEGER DEFAULT 0,
    emails_found   INTEGER DEFAULT 0,
    error          TEXT,
    scraped_at     TEXT
);

CREATE TABLE IF NOT EXISTS emails (
    id                  SERIAL PRIMARY KEY,
    org_id              {_ORG_DEFAULT},
    domain_id           INTEGER REFERENCES domains(id),
    domain              TEXT NOT NULL,
    email               TEXT NOT NULL,
    source_url          TEXT,
    source_type         TEXT,
    confidence          TEXT,
    is_role             INTEGER DEFAULT 0,
    category            TEXT,
    verification_status TEXT,
    extracted_at        TEXT
);

CREATE TABLE IF NOT EXISTS leads (
    id             SERIAL PRIMARY KEY,
    org_id         {_ORG_DEFAULT},
    source         TEXT NOT NULL,
    run_id         INTEGER,
    business_name  TEXT,
    domain         TEXT,
    email          TEXT,
    phone          TEXT,
    website        TEXT,
    address        TEXT,
    city           TEXT,
    country        TEXT,
    rating         REAL,
    reviews        INTEGER,
    status            TEXT DEFAULT 'new',
    notes             TEXT,
    homepage_snippet  TEXT,
    opener            TEXT,
    contact_name      TEXT,
    maps_url          TEXT,
    improvement_note  TEXT,
    lead_line         TEXT,
    last_review_days  INTEGER,
    created_at        TEXT
);

CREATE TABLE IF NOT EXISTS email_templates (
    id          SERIAL PRIMARY KEY,
    org_id      {_ORG_DEFAULT},
    name        TEXT NOT NULL,
    subject     TEXT NOT NULL,
    body        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id           SERIAL PRIMARY KEY,
    org_id       {_ORG_DEFAULT},
    name         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'active',
    leads_count  INTEGER DEFAULT 0,
    created_at   TEXT NOT NULL,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS campaign_steps (
    id            SERIAL PRIMARY KEY,
    campaign_id   INTEGER NOT NULL REFERENCES campaigns(id),
    step_number   INTEGER NOT NULL,
    delay_days    INTEGER NOT NULL,
    template_id   INTEGER NOT NULL REFERENCES email_templates(id)
);

CREATE TABLE IF NOT EXISTS scheduled_sends (
    id             SERIAL PRIMARY KEY,
    org_id         {_ORG_DEFAULT},
    campaign_id    INTEGER NOT NULL REFERENCES campaigns(id),
    step_id        INTEGER NOT NULL REFERENCES campaign_steps(id),
    step_number    INTEGER NOT NULL,
    lead_id        INTEGER NOT NULL REFERENCES leads(id),
    scheduled_at   TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    sent_at        TEXT,
    error          TEXT,
    account_id     INTEGER,
    claimed_at     TEXT
);

CREATE TABLE IF NOT EXISTS email_events (
    id            SERIAL PRIMARY KEY,
    org_id        {_ORG_DEFAULT},
    lead_id       INTEGER NOT NULL REFERENCES leads(id),
    campaign_id   INTEGER REFERENCES campaigns(id),
    event_type    TEXT NOT NULL,
    subject       TEXT,
    message_id    TEXT,
    note          TEXT,
    account_id    INTEGER,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outreach_settings (
    org_id {_ORG_DEFAULT},
    key    TEXT NOT NULL,
    value  TEXT NOT NULL,
    PRIMARY KEY (org_id, key)
);

CREATE TABLE IF NOT EXISTS email_accounts (
    id                  SERIAL PRIMARY KEY,
    org_id              {_ORG_DEFAULT},
    label               TEXT,
    from_name           TEXT,
    from_email          TEXT NOT NULL,
    reply_to            TEXT,
    smtp_host           TEXT NOT NULL,
    smtp_port           INTEGER NOT NULL DEFAULT 587,
    smtp_user           TEXT,
    smtp_password_enc   TEXT,
    smtp_use_tls        BOOLEAN NOT NULL DEFAULT TRUE,
    imap_host           TEXT,
    imap_port           INTEGER NOT NULL DEFAULT 993,
    imap_user           TEXT,
    imap_password_enc   TEXT,
    imap_mailbox        TEXT DEFAULT 'INBOX',
    daily_limit         INTEGER NOT NULL DEFAULT 50,
    hourly_limit        INTEGER NOT NULL DEFAULT 10,
    warmup_started_at   TEXT,
    warmup_days         INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'active',
    last_used_at        TEXT,
    created_at          TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_email_accounts_org_from
    ON email_accounts(org_id, from_email);

CREATE TABLE IF NOT EXISTS email_account_reservations (
    reservation_key TEXT PRIMARY KEY,
    org_id          {_ORG_DEFAULT},
    account_id      INTEGER NOT NULL REFERENCES email_accounts(id),
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_account_reservations_expiry
    ON email_account_reservations(account_id, expires_at);

CREATE TABLE IF NOT EXISTS vendor_threads (
    id                SERIAL PRIMARY KEY,
    org_id            {_ORG_DEFAULT},
    lead_id           INTEGER NOT NULL UNIQUE REFERENCES leads(id),
    subject           TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    price_floor       REAL,
    currency          TEXT DEFAULT 'USD',
    goal              TEXT,
    last_inbound_at   TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS negotiation_messages (
    id            SERIAL PRIMARY KEY,
    thread_id     INTEGER NOT NULL REFERENCES vendor_threads(id),
    direction     TEXT NOT NULL,
    subject       TEXT,
    body          TEXT,
    message_id    TEXT UNIQUE,
    in_reply_to   TEXT,
    from_addr     TEXT,
    to_addr       TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS negotiation_drafts (
    id                 SERIAL PRIMARY KEY,
    thread_id          INTEGER NOT NULL REFERENCES vendor_threads(id),
    model              TEXT,
    draft_subject      TEXT,
    draft_body         TEXT,
    detected_price     REAL,
    detected_currency  TEXT,
    reasoning          TEXT,
    suggested_action   TEXT,
    status             TEXT NOT NULL DEFAULT 'pending',
    created_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
    id            SERIAL PRIMARY KEY,
    org_id        {_ORG_DEFAULT},
    lead_id       INTEGER NOT NULL REFERENCES leads(id),
    thread_id     INTEGER REFERENCES vendor_threads(id),
    vendor_name   TEXT,
    final_price   REAL,
    currency      TEXT,
    terms         TEXT,
    notes         TEXT,
    closed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_replies (
    id                SERIAL PRIMARY KEY,
    org_id            {_ORG_DEFAULT},
    lead_id           INTEGER NOT NULL REFERENCES leads(id),
    thread_id         INTEGER REFERENCES vendor_threads(id),
    subject           TEXT,
    parent_message_id TEXT,
    parent_references TEXT,
    due_at            TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',
    error             TEXT,
    created_at        TEXT NOT NULL,
    sent_at           TEXT
);

CREATE TABLE IF NOT EXISTS outbound_messages (
    id               SERIAL PRIMARY KEY,
    org_id           {_ORG_DEFAULT},
    source           TEXT NOT NULL,
    lead_id          INTEGER REFERENCES leads(id),
    campaign_id      INTEGER REFERENCES campaigns(id),
    send_id          INTEGER REFERENCES scheduled_sends(id),
    thread_id        INTEGER REFERENCES vendor_threads(id),
    draft_id         INTEGER REFERENCES negotiation_drafts(id),
    pending_reply_id INTEGER REFERENCES pending_replies(id),
    to_email         TEXT NOT NULL,
    subject          TEXT NOT NULL,
    body             TEXT NOT NULL,
    reply_to         TEXT,
    in_reply_to      TEXT,
    references_hdr   TEXT,
    account_id       INTEGER,
    idempotency_key  TEXT NOT NULL UNIQUE,
    message_id       TEXT,
    status           TEXT NOT NULL DEFAULT 'pending',
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_by        TEXT,
    lease_expires_at TIMESTAMPTZ,
    last_error       TEXT,
    dead_lettered_at TIMESTAMPTZ,
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_outbound_due
    ON outbound_messages(org_id, status, next_attempt_at)
    WHERE status IN ('pending', 'retry');

CREATE INDEX IF NOT EXISTS idx_outbound_lead
    ON outbound_messages(org_id, lead_id);

CREATE INDEX IF NOT EXISTS idx_outbound_send
    ON outbound_messages(send_id);

CREATE TABLE IF NOT EXISTS outbound_domain_reservations (
    outbound_id INTEGER PRIMARY KEY REFERENCES outbound_messages(id) ON DELETE CASCADE,
    org_id      {_ORG_DEFAULT},
    domain      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outbound_domain_reservations
    ON outbound_domain_reservations(org_id, domain, expires_at);

CREATE TABLE IF NOT EXISTS outbound_provider_state (
    org_id       {_ORG_DEFAULT},
    provider_key TEXT NOT NULL,
    paused_until TIMESTAMPTZ NOT NULL,
    pause_reason TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, provider_key)
);

CREATE TABLE IF NOT EXISTS imap_cursors (
    org_id       {_ORG_DEFAULT},
    source_key   TEXT NOT NULL,
    mailbox      TEXT NOT NULL,
    uid_validity BIGINT NOT NULL,
    last_uid     BIGINT NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, source_key, mailbox)
);

CREATE TABLE IF NOT EXISTS inbound_messages (
    id             BIGSERIAL PRIMARY KEY,
    org_id         {_ORG_DEFAULT},
    source_key     TEXT NOT NULL,
    mailbox        TEXT NOT NULL,
    uid_validity   BIGINT NOT NULL,
    uid            BIGINT NOT NULL,
    raw_message    BYTEA NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'processing', 'processed', 'ignored', 'retry', 'dead_letter')),
    attempts       INTEGER NOT NULL DEFAULT 0,
    locked_by      TEXT,
    lease_expires_at TIMESTAMPTZ,
    last_error     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at   TIMESTAMPTZ,
    UNIQUE (org_id, source_key, mailbox, uid_validity, uid)
);

CREATE INDEX IF NOT EXISTS idx_inbound_messages_due
    ON inbound_messages(org_id, status, id)
    WHERE status IN ('pending', 'retry');

CREATE TABLE IF NOT EXISTS suppression_list (
    id         SERIAL PRIMARY KEY,
    org_id     {_ORG_DEFAULT},
    email      TEXT NOT NULL,
    domain     TEXT,
    reason     TEXT,
    source     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (org_id, email)
);

CREATE INDEX IF NOT EXISTS idx_suppression_domain
    ON suppression_list(org_id, domain);

CREATE TABLE IF NOT EXISTS worker_heartbeat (
    id         INTEGER PRIMARY KEY DEFAULT 1,
    pid        INTEGER,
    started_at TEXT,
    updated_at TEXT,
    jobs       TEXT DEFAULT '{{}}'
);

CREATE TABLE IF NOT EXISTS calls (
    id              SERIAL PRIMARY KEY,
    org_id          {_ORG_DEFAULT},
    lead_id         INTEGER NOT NULL REFERENCES leads(id),
    vapi_call_id    TEXT UNIQUE,
    status          TEXT NOT NULL DEFAULT 'queued',
    duration_sec    INTEGER,
    transcript      TEXT,
    summary         TEXT,
    recording_url   TEXT,
    qualified       TEXT,
    notes           TEXT,
    ended_reason    TEXT,
    reached_decision_maker BOOLEAN,
    pain_point      TEXT,
    pms_mentioned   TEXT,
    next_step       TEXT,
    next_step_time  TEXT,
    callback_requested BOOLEAN,
    scheduled_at    TEXT NOT NULL,
    initiated_at    TEXT,
    ended_at        TEXT,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_calls_lead         ON calls(lead_id);
CREATE INDEX IF NOT EXISTS idx_calls_status       ON calls(status);
CREATE INDEX IF NOT EXISTS idx_calls_vapi         ON calls(vapi_call_id);
CREATE INDEX IF NOT EXISTS idx_emails_domain      ON emails(domain);
CREATE INDEX IF NOT EXISTS idx_leads_source       ON leads(source);
CREATE INDEX IF NOT EXISTS idx_leads_status       ON leads(status);
CREATE INDEX IF NOT EXISTS idx_domains_run        ON domains(run_id);
CREATE INDEX IF NOT EXISTS idx_sched_status       ON scheduled_sends(status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_sched_lead         ON scheduled_sends(lead_id);
CREATE INDEX IF NOT EXISTS idx_sched_pending_org_due
    ON scheduled_sends(org_id, scheduled_at) WHERE status='pending';
CREATE INDEX IF NOT EXISTS idx_events_lead        ON email_events(lead_id);
CREATE INDEX IF NOT EXISTS idx_threads_lead       ON vendor_threads(lead_id);
ALTER TABLE vendor_threads ADD COLUMN IF NOT EXISTS last_intent TEXT;
CREATE INDEX IF NOT EXISTS idx_threads_intent     ON vendor_threads(last_intent);
CREATE INDEX IF NOT EXISTS idx_nmsgs_thread       ON negotiation_messages(thread_id);
CREATE INDEX IF NOT EXISTS idx_drafts_thread      ON negotiation_drafts(thread_id, status);
CREATE INDEX IF NOT EXISTS idx_pending_due        ON pending_replies(status, due_at);
CREATE INDEX IF NOT EXISTS idx_pending_lead       ON pending_replies(lead_id);

CREATE TABLE IF NOT EXISTS scrape_schedules (
    id            SERIAL PRIMARY KEY,
    org_id        {_ORG_DEFAULT},
    user_id       TEXT NOT NULL,
    niche         TEXT NOT NULL,
    country       TEXT NOT NULL,
    target_leads  INTEGER DEFAULT 20,
    status        TEXT DEFAULT 'active',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scrape_queries (
    id              SERIAL PRIMARY KEY,
    org_id          {_ORG_DEFAULT},
    niche           TEXT NOT NULL,
    city            TEXT NOT NULL,
    country         TEXT NOT NULL,
    lead_count      INTEGER DEFAULT 0,
    dedup_rate      REAL,
    last_scraped_at TIMESTAMP,
    next_run_at     TIMESTAMP,
    last_error      TEXT,
    runs_total      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS country_cities (
    country     TEXT PRIMARY KEY,
    cities      TEXT NOT NULL,
    fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_accounts_org       ON email_accounts(org_id, status);
"""


_MIGRATIONS = [
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS maps_url TEXT",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS improvement_note TEXT",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS lead_line TEXT",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_review_days INTEGER",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS reviews INTEGER",
    "ALTER TABLE leads ADD COLUMN IF NOT EXISTS do_not_contact BOOLEAN DEFAULT FALSE",
    "CREATE TABLE IF NOT EXISTS outreach_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS reached_decision_maker BOOLEAN",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS pain_point TEXT",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS pms_mentioned TEXT",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS next_step TEXT",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS next_step_time TEXT",
    "ALTER TABLE calls ADD COLUMN IF NOT EXISTS callback_requested BOOLEAN",
    "ALTER TABLE memberships ADD COLUMN IF NOT EXISTS email TEXT",
    "ALTER TABLE memberships ALTER COLUMN role SET DEFAULT 'user'",
    "UPDATE memberships SET role='admin' WHERE role='owner'",
    "UPDATE memberships SET role='user' WHERE role='member'",
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='memberships'::regclass AND conname='memberships_role_check') THEN ALTER TABLE memberships ADD CONSTRAINT memberships_role_check CHECK (role IN ('admin', 'user')); END IF; END $$",
    "CREATE TABLE IF NOT EXISTS platform_roles (user_id UUID PRIMARY KEY, role TEXT NOT NULL CHECK (role = 'super_admin'), created_at TEXT NOT NULL DEFAULT now()::text)",
    "CREATE TABLE IF NOT EXISTS organization_invitations (id UUID PRIMARY KEY, org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE, invited_user_id UUID, email TEXT NOT NULL, token_hash TEXT, expires_at TIMESTAMPTZ, role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin', 'user')), invited_by UUID NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'accepted', 'revoked')), created_at TEXT NOT NULL, accepted_at TEXT)",
    "ALTER TABLE organization_invitations ALTER COLUMN invited_user_id DROP NOT NULL",
    "ALTER TABLE organization_invitations ADD COLUMN IF NOT EXISTS token_hash TEXT",
    "ALTER TABLE organization_invitations ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
    "DROP INDEX IF EXISTS uq_org_invites_pending",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_org_invites_pending ON organization_invitations(org_id, lower(email)) WHERE status='pending'",
    "CREATE TABLE IF NOT EXISTS deletion_jobs (id UUID PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('organization', 'user')), target_id UUID NOT NULL, target_label TEXT NOT NULL, requested_by UUID NOT NULL, status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'processing', 'completed', 'failed')), attempts INTEGER NOT NULL DEFAULT 0, error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), available_at TIMESTAMPTZ NOT NULL DEFAULT now(), lease_expires_at TIMESTAMPTZ, locked_by UUID, completed_at TIMESTAMPTZ)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_deletion_jobs_active_target ON deletion_jobs(kind, target_id) WHERE status IN ('queued', 'processing')",
    "CREATE INDEX IF NOT EXISTS idx_deletion_jobs_ready ON deletion_jobs(status, available_at, created_at)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_memberships_org_email ON memberships(org_id, lower(email)) WHERE email IS NOT NULL",
    "CREATE TABLE IF NOT EXISTS coordination_leases (lease_key TEXT PRIMARY KEY, owner_token UUID NOT NULL, expires_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())",
    "CREATE TABLE IF NOT EXISTS outreach_dispatch_state (org_id UUID PRIMARY KEY REFERENCES organizations(id) ON DELETE CASCADE, next_send_at TIMESTAMPTZ NOT NULL DEFAULT now(), paused_until TIMESTAMPTZ, pause_reason TEXT)",
    # Performance indexes — safe to re-run (IF NOT EXISTS)
    "CREATE INDEX IF NOT EXISTS idx_leads_domain   ON leads(domain)     WHERE domain   IS NOT NULL",
    "CREATE INDEX IF NOT EXISTS idx_domains_domain ON domains(domain)",
    "CREATE INDEX IF NOT EXISTS idx_leads_maps_url ON leads(maps_url)   WHERE maps_url IS NOT NULL",
    # ── Multi-tenant foundation ───────────────────────────────
    f"INSERT INTO organizations (id, name, slug, status, plan, created_at) "
    f"VALUES ('{DEFAULT_ORG_ID}', 'Default Organization', 'default', 'active', 'free', now()::text) "
    f"ON CONFLICT DO NOTHING",
    "ALTER TABLE organizations ADD COLUMN IF NOT EXISTS hourly_allocation_limit "
    "INTEGER NOT NULL DEFAULT 60",
    "DO $$ BEGIN "
    "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='organizations'::regclass "
    "AND conname='organizations_hourly_allocation_limit_check') THEN "
    "ALTER TABLE organizations ADD CONSTRAINT organizations_hourly_allocation_limit_check "
    "CHECK (hourly_allocation_limit BETWEEN 1 AND 3600); "
    "END IF; END $$",
    "ALTER TABLE outreach_dispatch_state ADD COLUMN IF NOT EXISTS allocation_hour TIMESTAMPTZ",
    "ALTER TABLE outreach_dispatch_state ADD COLUMN IF NOT EXISTS last_slot_index INTEGER",
    "ALTER TABLE outreach_dispatch_state ADD COLUMN IF NOT EXISTS allocated_count "
    "INTEGER NOT NULL DEFAULT 0",
    f"ALTER TABLE extraction_runs  ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE domains          ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE emails           ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE leads            ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE email_templates  ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE campaigns        ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE scheduled_sends  ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE email_events     ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE vendor_threads   ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE pending_replies  ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE calls            ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE scrape_schedules ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE scrape_queries   ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    f"ALTER TABLE deals            ADD COLUMN IF NOT EXISTS org_id {_ORG_DEFAULT}",
    "ALTER TABLE outreach_settings ADD COLUMN IF NOT EXISTS org_id UUID",
    f"UPDATE outreach_settings SET org_id='{DEFAULT_ORG_ID}' WHERE org_id IS NULL",
    "ALTER TABLE outreach_settings ALTER COLUMN org_id SET DEFAULT "
    f"'{DEFAULT_ORG_ID}'::uuid",
    "ALTER TABLE outreach_settings ALTER COLUMN org_id SET NOT NULL",
    "ALTER TABLE outreach_settings DROP CONSTRAINT IF EXISTS outreach_settings_pkey",
    "DO $$ BEGIN "
    "IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='outreach_settings'::regclass AND contype='p') "
    "THEN ALTER TABLE outreach_settings ADD PRIMARY KEY (org_id, key); END IF; "
    "END $$",
    "ALTER TABLE emails DROP CONSTRAINT IF EXISTS emails_domain_email_key",
    "ALTER TABLE scrape_queries DROP CONSTRAINT IF EXISTS scrape_queries_niche_city_country_key",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_emails_org_domain_email ON emails(org_id, domain, email)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_scrape_q_org ON scrape_queries(org_id, niche, city, country)",
    "CREATE INDEX IF NOT EXISTS idx_leads_org ON leads(org_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_domains_org ON domains(org_id)",
    "CREATE INDEX IF NOT EXISTS idx_campaigns_org ON campaigns(org_id)",
    "CREATE INDEX IF NOT EXISTS idx_templates_org ON email_templates(org_id)",
    "CREATE INDEX IF NOT EXISTS idx_sched_org ON scheduled_sends(org_id, status, scheduled_at)",
    "CREATE INDEX IF NOT EXISTS idx_sched_pending_org_due "
    "ON scheduled_sends(org_id, scheduled_at) WHERE status='pending'",
    "CREATE INDEX IF NOT EXISTS idx_events_org ON email_events(org_id, lead_id)",
    "CREATE INDEX IF NOT EXISTS idx_threads_org ON vendor_threads(org_id)",
    "CREATE INDEX IF NOT EXISTS idx_pending_org ON pending_replies(org_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_calls_org ON calls(org_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_deals_org ON deals(org_id)",
    "CREATE INDEX IF NOT EXISTS idx_scrape_schedules_org ON scrape_schedules(org_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_scrape_queries_org ON scrape_queries(org_id, next_run_at)",
    # ── Multi-account sender ──────────────────────────────────
    "ALTER TABLE scheduled_sends ADD COLUMN IF NOT EXISTS account_id INTEGER",
    "ALTER TABLE scheduled_sends ADD COLUMN IF NOT EXISTS claimed_at TEXT",
    "ALTER TABLE email_events    ADD COLUMN IF NOT EXISTS account_id INTEGER",
    "ALTER TABLE outbound_messages ADD COLUMN IF NOT EXISTS dead_lettered_at TIMESTAMPTZ",
    "CREATE TABLE IF NOT EXISTS outbound_domain_reservations (outbound_id INTEGER PRIMARY KEY REFERENCES outbound_messages(id) ON DELETE CASCADE, org_id UUID NOT NULL, domain TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL)",
    "CREATE INDEX IF NOT EXISTS idx_outbound_domain_reservations ON outbound_domain_reservations(org_id, domain, expires_at)",
    "CREATE TABLE IF NOT EXISTS outbound_provider_state (org_id UUID NOT NULL, provider_key TEXT NOT NULL, paused_until TIMESTAMPTZ NOT NULL, pause_reason TEXT, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (org_id, provider_key))",
    "CREATE TABLE IF NOT EXISTS imap_cursors (org_id UUID NOT NULL, source_key TEXT NOT NULL, mailbox TEXT NOT NULL, uid_validity BIGINT NOT NULL, last_uid BIGINT NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (org_id, source_key, mailbox))",
    "CREATE TABLE IF NOT EXISTS inbound_messages (id BIGSERIAL PRIMARY KEY, org_id UUID NOT NULL, source_key TEXT NOT NULL, mailbox TEXT NOT NULL, uid_validity BIGINT NOT NULL, uid BIGINT NOT NULL, raw_message BYTEA NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'processed', 'ignored', 'retry', 'dead_letter')), attempts INTEGER NOT NULL DEFAULT 0, locked_by TEXT, lease_expires_at TIMESTAMPTZ, last_error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), processed_at TIMESTAMPTZ, UNIQUE (org_id, source_key, mailbox, uid_validity, uid))",
    "CREATE INDEX IF NOT EXISTS idx_inbound_messages_due ON inbound_messages(org_id, status, id) WHERE status IN ('pending', 'retry')",
    "CREATE INDEX IF NOT EXISTS idx_accounts_org   ON email_accounts(org_id, status)",
    "CREATE INDEX IF NOT EXISTS idx_events_account ON email_events(account_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_sched_account  ON scheduled_sends(account_id)",
]


def init_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it to .env:\n"
            "  DATABASE_URL=postgresql://user:pass@host:5432/postgres"
        )
    # psycopg uses extended-query mode, which can't handle multi-statement strings.
    # Split on semicolons and run each CREATE separately.
    statements = [s.strip() for s in SCHEMA.split(";") if s.strip()]
    with get_conn() as c:
        # Multiple API/worker instances can boot simultaneously against the
        # same PostgreSQL database; serialize schema setup inside the transaction.
        c.execute("SELECT pg_advisory_xact_lock(hashtext('lead_scraper:init_db'))")
        with c.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
            # Additive migrations — safe to re-run (IF NOT EXISTS)
            for stmt in _MIGRATIONS:
                cur.execute(stmt)
        configured_super_admins = [
            user_id.strip()
            for user_id in os.getenv("SUPER_ADMIN_USER_IDS", "").split(",")
            if user_id.strip()
        ]
        for user_id in configured_super_admins:
            try:
                user_id = str(__import__("uuid").UUID(user_id))
            except ValueError as exc:
                raise RuntimeError(
                    "SUPER_ADMIN_USER_IDS must contain comma-separated Supabase user UUIDs."
                ) from exc
            c.execute(
                "INSERT INTO platform_roles (user_id, role) "
                "VALUES (%s, 'super_admin') ON CONFLICT (user_id) "
                "DO UPDATE SET role='super_admin'",
                (user_id,),
            )


class OutreachDispatchBusy(Exception):
    pass


@contextmanager
def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set.")
    conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        # Supabase transaction pooler (port 5432) doesn't support prepared statements
        prepare_threshold=None,
        autocommit=False,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _utc_dt(value):
    """Coerce an ISO string/naive datetime into an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_email(email):
    return (email or "").strip().lower()


def _email_domain(email):
    email = _normalize_email(email)
    return email.rsplit("@", 1)[1] if "@" in email else None


def _scalar(cur):
    """Fetch first column of first row — used for COUNT(*) etc."""
    row = cur.fetchone()
    if not row:
        return 0
    return next(iter(row.values()))


def _scope(org_id, column):
    """Return (sql_suffix, params) filtering by org when one is supplied."""
    if org_id:
        return f" AND {column}=%s", [org_id]
    return "", []


def _lead_org(c, lead_id):
    row = c.execute("SELECT org_id FROM leads WHERE id=%s", (lead_id,)).fetchone()
    return row["org_id"] if row else None


def _thread_org(c, thread_id):
    row = c.execute("SELECT org_id FROM vendor_threads WHERE id=%s", (thread_id,)).fetchone()
    return row["org_id"] if row else None


# ── Organizations / memberships ──────────────────────────────
def org_for_user(user_id, requested_org_id=None):
    """Return the org for a user (optionally validating a requested one)."""
    with get_conn() as c:
        if requested_org_id:
            row = c.execute(
                "SELECT m.org_id FROM memberships m "
                "JOIN organizations o ON o.id=m.org_id "
                "WHERE m.user_id=%s AND m.org_id=%s AND o.status='active'",
                (user_id, requested_org_id),
            ).fetchone()
            return str(row["org_id"]) if row else None
        row = c.execute(
            "SELECT m.org_id FROM memberships m "
            "JOIN organizations o ON o.id=m.org_id "
            "WHERE m.user_id=%s AND o.status='active' "
            "ORDER BY (m.role='admin') DESC, m.created_at ASC LIMIT 1",
            (user_id,),
        ).fetchone()
        return str(row["org_id"]) if row else None


def is_super_admin(user_id):
    with get_conn() as c:
        return c.execute(
            "SELECT 1 FROM platform_roles WHERE user_id=%s AND role='super_admin'",
            (user_id,),
        ).fetchone() is not None


def list_all_orgs():
    with get_conn() as c:
        rows = c.execute(
            "SELECT o.id, o.name, o.slug, o.status, 'super_admin' AS role "
            "FROM organizations o WHERE o.status='active' "
            "ORDER BY o.created_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]


def list_org_directory():
    with get_conn() as c:
        rows = c.execute(
            "SELECT o.id, o.name, o.slug, o.status, 'super_admin' AS role, "
            "(o.id=%s) AS deletion_protected "
            "FROM organizations o ORDER BY o.created_at ASC",
            (DEFAULT_ORG_ID,),
        ).fetchall()
        return [dict(row) for row in rows]


def organization_exists(org_id):
    with get_conn() as c:
        return c.execute(
            "SELECT 1 FROM organizations WHERE id=%s AND status='active'",
            (org_id,),
        ).fetchone() is not None


def enqueue_organization_deletion(org_id, requested_by):
    with get_conn() as c:
        c.execute("SELECT pg_advisory_xact_lock(776411)")
        is_admin = c.execute(
            "SELECT 1 FROM platform_roles WHERE user_id=%s AND role='super_admin'",
            (requested_by,),
        ).fetchone()
        if not is_admin:
            return "forbidden", None
        organization = c.execute(
            "SELECT name, status FROM organizations WHERE id=%s FOR UPDATE",
            (org_id,),
        ).fetchone()
        if not organization:
            return "not_found", None
        if str(org_id) == DEFAULT_ORG_ID:
            return "protected_default", None
        if organization["status"] == "active":
            active_count = c.execute(
                "SELECT COUNT(*) AS count FROM organizations WHERE status='active'"
            ).fetchone()["count"]
            if active_count <= 1:
                return "last_active_org", None
        existing = c.execute(
            "SELECT id FROM deletion_jobs WHERE kind='organization' AND target_id=%s "
            "AND status IN ('queued', 'processing')",
            (org_id,),
        ).fetchone()
        if existing:
            return "already_queued", str(existing["id"])
        active_jobs = c.execute(
            "SELECT EXISTS (SELECT 1 FROM deletion_jobs "
            "WHERE status IN ('queued', 'processing')) AS active"
        ).fetchone()["active"]
        job_id = uuid4()
        c.execute(
            "INSERT INTO deletion_jobs (id,kind,target_id,target_label,requested_by) "
            "VALUES (%s,'organization',%s,%s,%s)",
            (job_id, org_id, organization["name"], requested_by),
        )
        return ("process_now" if not active_jobs else "queued"), str(job_id)


def enqueue_user_deletion(target_user_id, requested_by):
    with get_conn() as c:
        c.execute("SELECT pg_advisory_xact_lock(776411)")
        c.execute("LOCK TABLE platform_roles IN EXCLUSIVE MODE")
        c.execute("SELECT id FROM organizations ORDER BY id FOR UPDATE")
        actor_is_admin = c.execute(
            "SELECT 1 FROM platform_roles WHERE user_id=%s AND role='super_admin'",
            (requested_by,),
        ).fetchone() is not None
        if not actor_is_admin:
            return "forbidden", None
        if str(target_user_id) == str(requested_by):
            return "self_delete", None

        target = c.execute(
            "SELECT id, email FROM auth.users WHERE id=%s",
            (target_user_id,),
        ).fetchone()
        if not target:
            return "not_found", None
        is_super_admin = c.execute(
            "SELECT 1 FROM platform_roles WHERE user_id=%s AND role='super_admin'",
            (target_user_id,),
        ).fetchone() is not None
        if is_super_admin:
            super_admin_count = c.execute(
                "SELECT COUNT(*) AS count FROM platform_roles WHERE role='super_admin'"
            ).fetchone()["count"]
            if super_admin_count <= 1:
                return "last_super_admin", None
        sole_admin = c.execute(
            "SELECT 1 FROM memberships m "
            "JOIN organizations o ON o.id=m.org_id "
            "WHERE m.user_id=%s AND m.role='admin' AND o.status='active' "
            "AND (SELECT COUNT(*) FROM memberships other_members "
            "WHERE other_members.org_id=m.org_id AND other_members.role='admin')=1 "
            "LIMIT 1",
            (target_user_id,),
        ).fetchone()
        if sole_admin:
            return "last_org_admin", None
        existing = c.execute(
            "SELECT id FROM deletion_jobs WHERE kind='user' AND target_id=%s "
            "AND status IN ('queued', 'processing')",
            (target_user_id,),
        ).fetchone()
        if existing:
            return "already_queued", str(existing["id"])
        active_jobs = c.execute(
            "SELECT EXISTS (SELECT 1 FROM deletion_jobs "
            "WHERE status IN ('queued', 'processing')) AS active"
        ).fetchone()["active"]
        job_id = uuid4()
        c.execute(
            "INSERT INTO deletion_jobs (id,kind,target_id,target_label,requested_by) "
            "VALUES (%s,'user',%s,%s,%s)",
            (job_id, target_user_id, target["email"] or str(target_user_id), requested_by),
        )
        return ("process_now" if not active_jobs else "queued"), str(job_id)


def claim_deletion_job(worker_id, job_id=None):
    with get_conn() as c:
        c.execute(
            "UPDATE deletion_jobs SET status='failed', error='Worker stopped after final retry', "
            "updated_at=now(), lease_expires_at=NULL, locked_by=NULL "
            "WHERE status='processing' AND lease_expires_at < now() AND attempts >= 5"
        )
        if job_id:
            job = c.execute(
                "SELECT id, kind, target_id, target_label, requested_by, attempts "
                "FROM deletion_jobs WHERE id=%s AND attempts < 5 AND ("
                "(status='queued' AND available_at <= now()) OR "
                "(status='processing' AND lease_expires_at < now())) "
                "FOR UPDATE SKIP LOCKED",
                (job_id,),
            ).fetchone()
        else:
            job = c.execute(
                "SELECT id, kind, target_id, target_label, requested_by, attempts "
                "FROM deletion_jobs WHERE attempts < 5 AND ("
                "(status='queued' AND available_at <= now()) OR "
                "(status='processing' AND lease_expires_at < now())) "
                "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
            ).fetchone()
        if not job:
            return None
        updated = c.execute(
            "UPDATE deletion_jobs SET status='processing', attempts=attempts+1, "
            "updated_at=now(), lease_expires_at=now() + interval '5 minutes', "
            "locked_by=%s, error=NULL WHERE id=%s RETURNING attempts",
            (worker_id, job["id"]),
        ).fetchone()
        result = dict(job)
        result["id"] = str(result["id"])
        result["target_id"] = str(result["target_id"])
        result["requested_by"] = str(result["requested_by"])
        result["attempts"] = updated["attempts"]
        return result


def get_deletion_job_status(job_id):
    with get_conn() as c:
        row = c.execute(
            "SELECT status FROM deletion_jobs WHERE id=%s",
            (job_id,),
        ).fetchone()
        return row["status"] if row else None


def renew_deletion_job(job_id, worker_id):
    with get_conn() as c:
        row = c.execute(
            "UPDATE deletion_jobs SET lease_expires_at=now() + interval '5 minutes', "
            "updated_at=now() WHERE id=%s AND status='processing' AND locked_by=%s "
            "RETURNING id",
            (job_id, worker_id),
        ).fetchone()
        return row is not None


def complete_deletion_job(job_id, worker_id):
    with get_conn() as c:
        row = c.execute(
            "UPDATE deletion_jobs SET status='completed', completed_at=now(), "
            "updated_at=now(), lease_expires_at=NULL, locked_by=NULL, error=NULL "
            "WHERE id=%s AND status='processing' AND locked_by=%s RETURNING id",
            (job_id, worker_id),
        ).fetchone()
        return row is not None


def fail_deletion_job(job_id, worker_id, error, attempts):
    status = "failed" if attempts >= 5 else "queued"
    delay_seconds = min(600, 5 * (2 ** max(0, attempts - 1)))
    with get_conn() as c:
        row = c.execute(
            "UPDATE deletion_jobs SET status=%s, error=%s, updated_at=now(), "
            "available_at=now() + (%s * interval '1 second'), "
            "lease_expires_at=NULL, locked_by=NULL "
            "WHERE id=%s AND status='processing' AND locked_by=%s RETURNING id",
            (status, error[:1000], delay_seconds, job_id, worker_id),
        ).fetchone()
        return row is not None


def list_deletion_jobs(limit=50):
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, kind, target_id, target_label, status, attempts, error, "
            "created_at, updated_at, completed_at "
            "FROM deletion_jobs ORDER BY created_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        item["target_id"] = str(item["target_id"])
        for field in ("created_at", "updated_at", "completed_at"):
            if item[field]:
                item[field] = item[field].isoformat()
        result.append(item)
    return result


def delete_organization(org_id):
    with get_conn() as c:
        c.execute("SELECT pg_advisory_xact_lock(776411)")
        organization = c.execute(
            "SELECT status FROM organizations WHERE id=%s FOR UPDATE",
            (org_id,),
        ).fetchone()
        if not organization:
            return "not_found"
        if str(org_id) == DEFAULT_ORG_ID:
            return "protected_default"
        if organization["status"] == "active":
            active_count = c.execute(
                "SELECT COUNT(*) AS count FROM organizations WHERE status='active'"
            ).fetchone()["count"]
            if active_count <= 1:
                return "last_active_org"

        c.execute(
            "DELETE FROM scheduled_sends WHERE org_id=%s",
            (org_id,),
        )
        c.execute(
            "DELETE FROM email_events WHERE org_id=%s",
            (org_id,),
        )
        c.execute("DELETE FROM calls WHERE org_id=%s", (org_id,))
        c.execute("DELETE FROM deals WHERE org_id=%s", (org_id,))
        c.execute("DELETE FROM pending_replies WHERE org_id=%s", (org_id,))
        c.execute(
            "DELETE FROM negotiation_messages WHERE thread_id IN "
            "(SELECT id FROM vendor_threads WHERE org_id=%s)",
            (org_id,),
        )
        c.execute(
            "DELETE FROM negotiation_drafts WHERE thread_id IN "
            "(SELECT id FROM vendor_threads WHERE org_id=%s)",
            (org_id,),
        )
        c.execute(
            "DELETE FROM campaign_steps WHERE campaign_id IN "
            "(SELECT id FROM campaigns WHERE org_id=%s)",
            (org_id,),
        )
        c.execute("DELETE FROM vendor_threads WHERE org_id=%s", (org_id,))
        c.execute("DELETE FROM emails WHERE org_id=%s", (org_id,))
        c.execute(
            "DELETE FROM email_account_reservations WHERE org_id=%s",
            (org_id,),
        )

        scoped_tables = c.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name <> 'organizations' "
            "AND table_name NOT IN ('memberships', 'organization_invitations') "
            "AND column_name='org_id' ORDER BY table_name"
        ).fetchall()
        for row in scoped_tables:
            c.execute(
                psycopg_sql.SQL("DELETE FROM {} WHERE org_id=%s").format(
                    psycopg_sql.Identifier(row["table_name"])
                ),
                (org_id,),
            )

        c.execute(
            "DELETE FROM organizations WHERE id=%s",
            (org_id,),
        )
    return "ok"


@contextmanager
def global_user_deletion(target_user_id, actor_user_id, allow_missing_auth=False):
    """Lock relevant access records while a platform admin deletes an auth user."""
    with get_conn() as c:
        c.execute("LOCK TABLE platform_roles IN EXCLUSIVE MODE")
        c.execute("SELECT id FROM organizations ORDER BY id FOR UPDATE")
        target = c.execute(
            "SELECT id, email FROM auth.users WHERE id=%s",
            (target_user_id,),
        ).fetchone()
        actor_is_super_admin = c.execute(
            "SELECT 1 FROM platform_roles WHERE user_id=%s AND role='super_admin'",
            (actor_user_id,),
        ).fetchone() is not None
        reason = "forbidden" if not actor_is_super_admin else (
            "not_found" if not target and not allow_missing_auth else None
        )
        is_super_admin = False
        if target:
            is_super_admin = c.execute(
                "SELECT 1 FROM platform_roles WHERE user_id=%s AND role='super_admin'",
                (target_user_id,),
            ).fetchone() is not None
        if target and is_super_admin:
            super_admin_count = c.execute(
                "SELECT COUNT(*) AS count FROM platform_roles WHERE role='super_admin'"
            ).fetchone()["count"]
            if super_admin_count <= 1:
                reason = "last_super_admin"
        if target and not reason:
            sole_admin = c.execute(
                "SELECT 1 FROM memberships m "
                "JOIN organizations o ON o.id=m.org_id "
                "WHERE m.user_id=%s AND m.role='admin' AND o.status='active' "
                "AND (SELECT COUNT(*) FROM memberships other_members "
                "WHERE other_members.org_id=m.org_id AND other_members.role='admin')=1 "
                "LIMIT 1",
                (target_user_id,),
            ).fetchone()
            if sole_admin:
                reason = "last_org_admin"

        yield {
            "connection": c,
            "reason": reason,
            "auth_user_exists": target is not None,
        }
        if not reason:
            c.execute(
                "DELETE FROM organization_invitations "
                "WHERE invited_user_id=%s OR invited_by=%s "
                "OR lower(email)=lower(%s)",
                (
                    target_user_id,
                    target_user_id,
                    target["email"] if target else None,
                ),
            )
            c.execute("DELETE FROM memberships WHERE user_id=%s", (target_user_id,))
            c.execute("DELETE FROM platform_roles WHERE user_id=%s", (target_user_id,))


def list_all_users(limit=100, offset=0):
    with get_conn() as c:
        visible_user_filter = (
            "EXISTS (SELECT 1 FROM memberships m WHERE m.user_id=u.id) "
            "OR EXISTS (SELECT 1 FROM platform_roles p WHERE p.user_id=u.id)"
        )
        total = c.execute(
            f"SELECT COUNT(*) AS count FROM auth.users u WHERE {visible_user_filter}"
        ).fetchone()["count"]
        rows = c.execute(
            "SELECT u.id, u.email, u.created_at, u.last_sign_in_at, "
            "u.email_confirmed_at IS NOT NULL AS email_verified, "
            "CASE WHEN EXISTS (SELECT 1 FROM platform_roles p "
            "WHERE p.user_id=u.id AND p.role='super_admin') "
            "THEN 'super_admin' ELSE NULL END AS platform_role "
            f"FROM auth.users u WHERE {visible_user_filter} "
            "ORDER BY u.created_at DESC LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()
        memberships = c.execute(
            "SELECT m.user_id, m.org_id, m.role, o.name AS org_name, "
            "'member' AS access_status "
            "FROM memberships m JOIN organizations o ON o.id=m.org_id "
            "WHERE m.user_id = ANY(%s)",
            ([row["id"] for row in rows],),
        ).fetchall() if rows else []
        if rows:
            pending = c.execute(
                "SELECT i.invited_user_id AS user_id, i.org_id, i.role, "
                "o.name AS org_name, 'pending' AS access_status "
                "FROM organization_invitations i "
                "JOIN organizations o ON o.id=i.org_id "
                "WHERE i.status='pending' AND i.invited_user_id = ANY(%s)",
                ([row["id"] for row in rows],),
            ).fetchall()
            memberships.extend(pending)
    by_user = {str(row["id"]): [] for row in rows}
    for membership in memberships:
        by_user.setdefault(str(membership["user_id"]), []).append({
            "org_id": str(membership["org_id"]),
            "org_name": membership["org_name"],
            "role": membership["role"],
            "access_status": membership["access_status"],
        })
    items = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item["id"])
        item["organizations"] = by_user.get(item["id"], [])
        items.append(item)
    return {"items": items, "total": total}


def pending_invitation_exists(org_id, email):
    with get_conn() as c:
        return c.execute(
            "SELECT 1 FROM organization_invitations "
            "WHERE org_id=%s AND lower(email)=lower(%s) AND status='pending'",
            (org_id, email),
        ).fetchone() is not None


def create_org_invitation(
    invitation_id, org_id, email, role, invited_by, token_hash, expires_at
):
    with get_conn() as c:
        c.execute(
            "INSERT INTO organization_invitations "
            "(id, org_id, email, token_hash, expires_at, role, invited_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                invitation_id, org_id, email, token_hash, expires_at,
                role, invited_by, now(),
            ),
        )


def hash_invitation_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def discard_org_invitation(invitation_id):
    with get_conn() as c:
        c.execute(
            "UPDATE organization_invitations SET status='revoked', "
            "token_hash=NULL, expires_at=NULL "
            "WHERE id=%s AND status='pending'",
            (invitation_id,),
        )


def lookup_org_invitation(token):
    token_hash = hash_invitation_token(token)
    with get_conn() as c:
        row = c.execute(
            "SELECT i.email, o.name AS organization_name, "
            "EXISTS (SELECT 1 FROM auth.users u "
            "WHERE lower(u.email)=lower(i.email)) AS account_exists "
            "FROM organization_invitations i "
            "JOIN organizations o ON o.id=i.org_id "
            "WHERE i.token_hash=%s AND i.status='pending' "
            "AND i.expires_at > now() AND o.status='active'",
            (token_hash,),
        ).fetchone()
        return dict(row) if row else None


def find_auth_user_by_email(email):
    with get_conn() as c:
        row = c.execute(
            "SELECT id, email, email_confirmed_at FROM auth.users "
            "WHERE lower(email)=lower(%s) ORDER BY created_at DESC LIMIT 1",
            (email.strip(),),
        ).fetchone()
        return dict(row) if row else None


def accept_org_invitation_token(user_id, email, token):
    token_hash = hash_invitation_token(token)
    normalized_email = email.strip().lower()
    with get_conn() as c:
        invitation = c.execute(
            "SELECT id, org_id, email, role, status, expires_at "
            "FROM organization_invitations WHERE token_hash=%s FOR UPDATE",
            (token_hash,),
        ).fetchone()
        if not invitation:
            return {"error": "not_found"}
        if invitation["status"] != "pending":
            return {"error": "not_pending"}
        if invitation["expires_at"] is None or invitation["expires_at"] <= datetime.now(timezone.utc):
            return {"error": "expired"}
        if invitation["email"].strip().lower() != normalized_email:
            return {"error": "email_mismatch"}
        active_org = c.execute(
            "SELECT 1 FROM organizations WHERE id=%s AND status='active'",
            (invitation["org_id"],),
        ).fetchone()
        if not active_org:
            return {"error": "not_found"}
        c.execute(
            "INSERT INTO memberships (user_id, org_id, role, email, created_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id, org_id) DO NOTHING",
            (user_id, invitation["org_id"], invitation["role"], normalized_email, now()),
        )
        c.execute(
            "UPDATE organization_invitations "
            "SET status='accepted', invited_user_id=%s, accepted_at=%s, "
            "token_hash=NULL, expires_at=NULL WHERE id=%s AND status='pending'",
            (user_id, now(), invitation["id"]),
        )
        return {
            "items": [{
                "org_id": str(invitation["org_id"]),
                "role": invitation["role"],
            }]
        }


def revoke_org_invitation(actor_id, org_id, invitation_or_user_id):
    with get_conn() as c:
        super_admin = c.execute(
            "SELECT 1 FROM platform_roles WHERE user_id=%s AND role='super_admin'",
            (actor_id,),
        ).fetchone() is not None
        actor = c.execute(
            "SELECT role FROM memberships WHERE org_id=%s AND user_id=%s",
            (org_id, actor_id),
        ).fetchone()
        if not super_admin and (not actor or actor["role"] != "admin"):
            return "forbidden"
        row = c.execute(
            "UPDATE organization_invitations SET status='revoked', "
            "token_hash=NULL, expires_at=NULL "
            "WHERE org_id=%s AND (id=%s OR invited_user_id=%s) "
            "AND status='pending' "
            "RETURNING id",
            (org_id, invitation_or_user_id, invitation_or_user_id),
        ).fetchone()
        return "ok" if row else "not_found"


def accept_org_invitations(user_id, email):
    normalized_email = email.strip().lower()
    accepted = []
    with get_conn() as c:
        invitations = c.execute(
            "SELECT id, org_id, role FROM organization_invitations "
            "WHERE invited_user_id=%s AND lower(email)=lower(%s) "
            "AND status='pending' AND EXISTS ("
            "SELECT 1 FROM organizations WHERE id=organization_invitations.org_id "
            "AND status='active') ORDER BY created_at FOR UPDATE",
            (user_id, normalized_email),
        ).fetchall()
        for invitation in invitations:
            c.execute(
                "INSERT INTO memberships (user_id, org_id, role, email, created_at) "
                "SELECT %s, %s, %s, %s, %s "
                "WHERE EXISTS (SELECT 1 FROM organizations WHERE id=%s AND status='active') "
                "ON CONFLICT (user_id, org_id) DO NOTHING",
                (
                    user_id, invitation["org_id"], invitation["role"],
                    normalized_email, now(), invitation["org_id"],
                ),
            )
            updated = c.execute(
                "UPDATE organization_invitations SET status='accepted', accepted_at=%s "
                "WHERE id=%s AND status='pending' RETURNING org_id, role",
                (now(), invitation["id"]),
            ).fetchone()
            if updated:
                accepted.append({
                    "org_id": str(updated["org_id"]),
                    "role": updated["role"],
                })
    return accepted


def has_pending_org_invitation_for_email(email):
    with get_conn() as c:
        return c.execute(
            "SELECT EXISTS ("
            "SELECT 1 FROM organization_invitations i "
            "JOIN organizations o ON o.id=i.org_id "
            "WHERE lower(i.email)=lower(%s) AND i.status='pending' "
            "AND o.status='active') AS pending",
            (email.strip(),),
        ).fetchone()["pending"]


def ensure_user_org(user_id, email=None):
    """Return the user's first org, creating a personal one if none exists.

    Uses the user's uuid as the org id so concurrent first requests can't
    create duplicate organizations.
    """
    existing = org_for_user(user_id)
    if existing:
        return existing
    name = (email.split("@")[0] if email else "My Company") or "My Company"
    import uuid as _uuid

    org_id = user_id
    slug = f"user-{user_id}"
    with get_conn() as c:
        existing_personal_org = c.execute(
            "SELECT status FROM organizations WHERE id=%s",
            (user_id,),
        ).fetchone()
        if existing_personal_org:
            org_id = str(_uuid.uuid4())
            slug = f"user-{user_id}-{org_id[:8]}"
        c.execute(
            "INSERT INTO organizations (id, name, slug, status, plan, created_at) "
            "VALUES (%s, %s, %s, 'active', 'free', %s) ON CONFLICT DO NOTHING",
            (org_id, name, slug, now()),
        )
        c.execute(
            "INSERT INTO memberships (user_id, org_id, role, email, created_at) "
            "VALUES (%s, %s, 'admin', %s, %s) "
            "ON CONFLICT (user_id, org_id) DO UPDATE "
            "SET email=COALESCE(EXCLUDED.email, memberships.email)",
            (user_id, org_id, email, now()),
        )
    return str(org_id)


def list_orgs_for_user(user_id):
    with get_conn() as c:
        rows = c.execute(
            "SELECT o.id, o.name, o.slug, o.status, m.role "
            "FROM memberships m JOIN organizations o ON o.id=m.org_id "
            "WHERE m.user_id=%s AND o.status='active' ORDER BY o.created_at ASC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def list_active_orgs():
    with get_conn() as c:
        rows = c.execute(
            "SELECT id FROM organizations WHERE status='active' ORDER BY created_at ASC"
        ).fetchall()
        return [r["id"] for r in rows]


def create_org(name, slug=None):
    import uuid as _uuid

    org_id = str(_uuid.uuid4())
    slug = slug or f"org-{org_id[:8]}"
    with get_conn() as c:
        c.execute(
            "INSERT INTO organizations (id, name, slug, status, plan, created_at) "
            "VALUES (%s, %s, %s, 'active', 'free', %s)",
            (org_id, name, slug, now()),
        )
    return org_id


def get_org_members(org_id):
    with get_conn() as c:
        rows = c.execute(
            "SELECT user_id, email, role, created_at, access_status FROM ("
            "SELECT user_id, email, role, created_at, 'member' AS access_status "
            "FROM memberships WHERE org_id=%s "
            "UNION ALL "
            "SELECT id AS user_id, email, role, created_at, "
            "'pending' AS access_status FROM organization_invitations "
            "WHERE org_id=%s AND status='pending'"
            ") AS organization_people "
            "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, created_at",
            (org_id, org_id),
        ).fetchall()
        return [dict(row) for row in rows]


def get_membership_role(user_id, org_id):
    with get_conn() as c:
        row = c.execute(
            "SELECT role FROM memberships WHERE user_id=%s AND org_id=%s",
            (user_id, org_id),
        ).fetchone()
        return row["role"] if row else None


def update_membership_email(user_id, org_id, email):
    if not email:
        return
    with get_conn() as c:
        c.execute(
            "UPDATE memberships SET email=%s WHERE user_id=%s AND org_id=%s",
            (email.strip().lower(), user_id, org_id),
        )


def membership_email_exists(org_id, email):
    with get_conn() as c:
        return c.execute(
            "SELECT 1 FROM memberships m "
            "LEFT JOIN auth.users u ON u.id=m.user_id "
            "WHERE m.org_id=%s AND lower(COALESCE(m.email, u.email))=lower(%s)",
            (org_id, email),
        ).fetchone() is not None


def add_org_member(user_id, org_id, email, role="user"):
    with get_conn() as c:
        c.execute(
            "INSERT INTO memberships (user_id, org_id, role, email, created_at) "
            "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (user_id, org_id) "
            "DO UPDATE SET email=COALESCE(EXCLUDED.email, memberships.email)",
            (user_id, org_id, role, email, now()),
        )


def update_org_member_role(actor_id, org_id, member_id, role):
    if role not in ("admin", "user"):
        raise ValueError("Invalid organization role.")
    with get_conn() as c:
        c.execute("SELECT id FROM organizations WHERE id=%s FOR UPDATE", (org_id,))
        super_admin = c.execute(
            "SELECT 1 FROM platform_roles WHERE user_id=%s AND role='super_admin'",
            (actor_id,),
        ).fetchone() is not None
        actor = c.execute(
            "SELECT role FROM memberships WHERE org_id=%s AND user_id=%s",
            (org_id, actor_id),
        ).fetchone()
        if not super_admin and (not actor or actor["role"] != "admin"):
            return "forbidden"
        target = c.execute(
            "SELECT role FROM memberships WHERE org_id=%s AND user_id=%s",
            (org_id, member_id),
        ).fetchone()
        if not target:
            return "not_found"
        if target["role"] == "admin" and role != "admin":
            admins = c.execute(
                "SELECT COUNT(*) AS count FROM memberships "
                "WHERE org_id=%s AND role='admin'",
                (org_id,),
            ).fetchone()["count"]
            if admins <= 1:
                return "last_admin"
        c.execute(
            "UPDATE memberships SET role=%s WHERE org_id=%s AND user_id=%s",
            (role, org_id, member_id),
        )
    return "ok"


def remove_org_member(actor_id, org_id, member_id):
    with get_conn() as c:
        c.execute("SELECT id FROM organizations WHERE id=%s FOR UPDATE", (org_id,))
        super_admin = c.execute(
            "SELECT 1 FROM platform_roles WHERE user_id=%s AND role='super_admin'",
            (actor_id,),
        ).fetchone() is not None
        actor = c.execute(
            "SELECT role FROM memberships WHERE org_id=%s AND user_id=%s",
            (org_id, actor_id),
        ).fetchone()
        if not super_admin and (not actor or actor["role"] != "admin"):
            return "forbidden"
        target = c.execute(
            "SELECT role FROM memberships WHERE org_id=%s AND user_id=%s",
            (org_id, member_id),
        ).fetchone()
        if not target:
            return "not_found"
        if target["role"] == "admin":
            owners = c.execute(
                "SELECT COUNT(*) AS count FROM memberships "
                "WHERE org_id=%s AND role='admin'",
                (org_id,),
            ).fetchone()["count"]
            if owners <= 1:
                return "last_admin"
        c.execute(
            "DELETE FROM memberships WHERE org_id=%s AND user_id=%s",
            (org_id, member_id),
        )
    return "ok"


def try_acquire_lease(lease_key, owner_token, ttl_seconds):
    with get_conn() as c:
        row = c.execute(
            "INSERT INTO coordination_leases "
            "(lease_key, owner_token, expires_at, updated_at) "
            "VALUES (%s, %s, now() + (%s * interval '1 second'), now()) "
            "ON CONFLICT (lease_key) DO UPDATE SET "
            "owner_token=EXCLUDED.owner_token, expires_at=EXCLUDED.expires_at, "
            "updated_at=now() "
            "WHERE coordination_leases.expires_at <= now() "
            "RETURNING lease_key",
            (lease_key, owner_token, ttl_seconds),
        ).fetchone()
        return row is not None


def renew_lease(lease_key, owner_token, ttl_seconds):
    with get_conn() as c:
        row = c.execute(
            "UPDATE coordination_leases SET "
            "expires_at=now() + (%s * interval '1 second'), updated_at=now() "
            "WHERE lease_key=%s AND owner_token=%s AND expires_at > now() "
            "RETURNING lease_key",
            (ttl_seconds, lease_key, owner_token),
        ).fetchone()
        return row is not None


def release_lease(lease_key, owner_token):
    with get_conn() as c:
        c.execute(
            "DELETE FROM coordination_leases WHERE lease_key=%s AND owner_token=%s",
            (lease_key, owner_token),
        )


# ── Runs ─────────────────────────────────────────────────────
def start_run(run_type, input_count=0, metadata=None, org_id=None):
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO extraction_runs (org_id, run_type, started_at, status, input_count, metadata) "
            "VALUES (%s, %s, %s, 'running', %s, %s) RETURNING id",
            (org_id_or_default(org_id), run_type, now(), input_count, json.dumps(metadata or {})),
        )
        return cur.fetchone()["id"]


def finish_run(run_id, output_count, status="completed", org_id=None):
    scope, scope_args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            "UPDATE extraction_runs SET finished_at=%s, status=%s, output_count=%s "
            f"WHERE id=%s{scope}",
            (now(), status, output_count, run_id, *scope_args),
        )


# ── Domains ──────────────────────────────────────────────────
def insert_domain(run_id, domain, status, has_mx, pages_visited, emails_found, error=None, org_id=None):
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO domains "
            "(org_id, run_id, domain, status, has_mx, pages_visited, emails_found, error, scraped_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (org_id_or_default(org_id), run_id, domain, status, int(bool(has_mx)),
             pages_visited, emails_found, error, now()),
        )
        return cur.fetchone()["id"]


# ── Emails ───────────────────────────────────────────────────
def insert_email(domain_id, domain, email, source_url, source_type, confidence,
                 is_role, has_mx, category=None, org_id=None):
    with get_conn() as c:
        try:
            c.execute(
                "INSERT INTO emails "
                "(org_id, domain_id, domain, email, source_url, source_type, confidence, "
                " is_role, category, verification_status, extracted_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    org_id_or_default(org_id),
                    domain_id, domain, email, source_url, source_type, confidence,
                    int(bool(is_role)),
                    category,
                    "valid_mx" if has_mx else "no_mx",
                    now(),
                ),
            )
        except pg_errors.UniqueViolation:
            pass


# ── Leads ────────────────────────────────────────────────────
def insert_lead(**kwargs):
    """Insert a lead and return its id. Deduplicates by (org_id, maps_url)."""
    kwargs.setdefault("created_at", now())
    kwargs["org_id"] = org_id_or_default(kwargs.get("org_id"))
    maps_url = kwargs.get("maps_url")
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join("%s" for _ in kwargs)
    with get_conn() as c:
        # Dedup: if this Maps URL was already scraped for this org, reuse it.
        if maps_url:
            row = c.execute(
                "SELECT id FROM leads WHERE org_id=%s AND maps_url=%s LIMIT 1",
                (kwargs["org_id"], maps_url),
            ).fetchone()
            if row:
                return row["id"]
        cur = c.execute(
            f"INSERT INTO leads ({cols}) VALUES ({placeholders}) RETURNING id",
            tuple(kwargs.values()),
        )
        return cur.fetchone()["id"]


# ── Analytics helpers ────────────────────────────────────────
def overview_stats(org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        def one(q, *a):
            return _scalar(c.execute(q, a))
        return {
            "leads_total":       one(f"SELECT COUNT(*) FROM leads WHERE 1=1{clause}", *args),
            "emails_total":      one(f"SELECT COUNT(*) FROM emails WHERE 1=1{clause}", *args),
            "domains_total":     one(f"SELECT COUNT(*) FROM domains WHERE 1=1{clause}", *args),
            "runs_total":        one(f"SELECT COUNT(*) FROM extraction_runs WHERE 1=1{clause}", *args),
            "emails_high_conf":  one(f"SELECT COUNT(*) FROM emails WHERE confidence='high'{clause}", *args),
            "emails_with_mx":    one(f"SELECT COUNT(*) FROM emails WHERE verification_status='valid_mx'{clause}", *args),
        }


def runs_summary(limit=20, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, run_type, started_at, finished_at, status, input_count, output_count "
            f"FROM extraction_runs WHERE 1=1{clause} ORDER BY id DESC LIMIT %s",
            (*args, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def emails_by_domain(limit=50, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        rows = c.execute(
            "SELECT domain, COUNT(*) AS email_count, "
            "SUM(CASE WHEN confidence='high' THEN 1 ELSE 0 END) AS high_conf "
            f"FROM emails WHERE 1=1{clause} GROUP BY domain ORDER BY email_count DESC LIMIT %s",
            (*args, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def recent_emails(limit=100, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        rows = c.execute(
            "SELECT domain, email, source_type, confidence, is_role, "
            "verification_status, extracted_at "
            f"FROM emails WHERE 1=1{clause} ORDER BY id DESC LIMIT %s",
            (*args, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def leads_by_status(org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        rows = c.execute(
            f"SELECT status, COUNT(*) AS n FROM leads WHERE 1=1{clause} GROUP BY status",
            tuple(args),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def emails_by_day(days=14, org_id=None):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        rows = c.execute(
            "SELECT SUBSTR(extracted_at, 1, 10) AS day, COUNT(*) AS n "
            f"FROM emails WHERE extracted_at >= %s{clause} "
            "GROUP BY day ORDER BY day",
            (cutoff, *args),
        ).fetchall()
        return [dict(r) for r in rows]


# ── Templates ────────────────────────────────────────────────
def upsert_template(name, subject, body, template_id=None, org_id=None):
    ts = now()
    org = org_id_or_default(org_id)
    with get_conn() as c:
        if template_id:
            c.execute(
                "UPDATE email_templates SET name=%s, subject=%s, body=%s, updated_at=%s "
                "WHERE id=%s AND org_id=%s",
                (name, subject, body, ts, template_id, org),
            )
            return template_id
        cur = c.execute(
            "INSERT INTO email_templates (org_id, name, subject, body, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
            (org, name, subject, body, ts, ts),
        )
        return cur.fetchone()["id"]


def list_templates(org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            f"SELECT * FROM email_templates WHERE 1=1{clause} ORDER BY updated_at DESC",
            tuple(args),
        ).fetchall()]


def get_template(template_id, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            f"SELECT * FROM email_templates WHERE id=%s{clause}", (template_id, *args)
        ).fetchone()
        return dict(row) if row else None


def delete_template(template_id, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(f"DELETE FROM email_templates WHERE id=%s{clause}", (template_id, *args))


# ── Leads (for selection) ────────────────────────────────────
def sendable_leads(source=None, status="new", limit=500, org_id=None):
    q = ("SELECT id, source, business_name, domain, email, website, status, created_at "
         "FROM leads WHERE email IS NOT NULL AND email != '' AND status=%s")
    args = [status]
    if source:
        q += " AND source=%s"
        args.append(source)
    clause, clause_args = _scope(org_id, "org_id")
    q += clause
    args.extend(clause_args)
    q += " ORDER BY id DESC LIMIT %s"
    args.append(limit)
    with get_conn() as c:
        return [dict(r) for r in c.execute(q, tuple(args)).fetchall()]


def get_lead(lead_id, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            f"SELECT * FROM leads WHERE id=%s{clause}", (lead_id, *args)
        ).fetchone()
        return dict(row) if row else None


def update_lead_status(lead_id, status, note=None, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            f"UPDATE leads SET status=%s WHERE id=%s{clause}",
            (status, lead_id, *args),
        )
        if note is not None:
            c.execute(
                f"UPDATE leads SET notes=COALESCE(notes,'') || %s WHERE id=%s{clause}",
                (f"\n[{now()}] {note}", lead_id, *args),
            )


# ── Campaigns ────────────────────────────────────────────────
def create_campaign(name, steps, lead_ids, notes=None, org_id=None):
    org = org_id_or_default(org_id)
    base = datetime.now(timezone.utc).replace(tzinfo=None)
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO campaigns (org_id, name, status, leads_count, created_at, notes) "
            "VALUES (%s, %s, 'active', 0, %s, %s) RETURNING id",
            (org, name, now(), notes),
        )
        campaign_id = cur.fetchone()["id"]

        step_ids = []
        for i, s in enumerate(steps, 1):
            cur = c.execute(
                "INSERT INTO campaign_steps "
                "(campaign_id, step_number, delay_days, template_id) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (campaign_id, i, s["delay_days"], s["template_id"]),
            )
            step_ids.append((cur.fetchone()["id"], i, s["delay_days"]))

        enrolled = 0
        for lead_id in lead_ids:
            # Skip leads marked do-not-contact and leads already sitting in
            # another active campaign — no double-emailing.
            already = c.execute(
                "SELECT 1 FROM leads l WHERE l.id=%s AND l.org_id=%s "
                "AND (l.do_not_contact IS TRUE "
                "OR EXISTS (SELECT 1 FROM scheduled_sends s "
                "JOIN campaigns cm ON cm.id=s.campaign_id "
                "WHERE s.lead_id=%s AND s.status='pending' AND cm.status='active'))",
                (lead_id, org, lead_id),
            ).fetchone()
            if already:
                continue

            cumulative = 0
            for step_id, step_num, delay in step_ids:
                cumulative += delay
                scheduled = (base + timedelta(days=cumulative)).isoformat(timespec="seconds")
                c.execute(
                    "INSERT INTO scheduled_sends "
                    "(org_id, campaign_id, step_id, step_number, lead_id, scheduled_at, status) "
                    "VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
                    (org, campaign_id, step_id, step_num, lead_id, scheduled),
                )
            enrolled += 1

        c.execute(
            "UPDATE campaigns SET leads_count=%s WHERE id=%s",
            (enrolled, campaign_id),
        )
        return campaign_id


def list_campaigns(org_id=None):
    clause, args = _scope(org_id, "c.org_id")
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM scheduled_sends s WHERE s.campaign_id=c.id AND s.status='sent') AS sent_count, "
            "(SELECT COUNT(*) FROM scheduled_sends s WHERE s.campaign_id=c.id AND s.status='pending') AS pending_count "
            f"FROM campaigns c WHERE 1=1{clause} ORDER BY c.id DESC",
            tuple(args),
        ).fetchall()]


def set_campaign_status(campaign_id, status, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            f"UPDATE campaigns SET status=%s WHERE id=%s{clause}",
            (status, campaign_id, *args),
        )


def campaign_steps(campaign_id, org_id=None):
    clause, args = _scope(org_id, "c.org_id")
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT cs.*, t.name AS template_name, t.subject, t.body "
            "FROM campaign_steps cs "
            "JOIN email_templates t ON t.id = cs.template_id "
            "JOIN campaigns c ON c.id = cs.campaign_id "
            f"WHERE cs.campaign_id=%s{clause} ORDER BY cs.step_number",
            (campaign_id, *args)
        ).fetchall()]


# ── Scheduled sends ──────────────────────────────────────────
def predecessor_send_status(lead_id, campaign_id, step_number, org_id=None):
    """Status of the immediately preceding step for this lead in this
    campaign, or None if there isn't one (step_number is 1, or the row is
    missing for some reason). Used to hold a step like 'Bump' or 'Value' from
    firing if the step before it never actually sent — those templates
    reference a prior email ("just bumping this...") that wouldn't exist.
    """
    if step_number <= 1:
        return None
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            "SELECT status FROM scheduled_sends "
            f"WHERE lead_id=%s AND campaign_id=%s AND step_number=%s{clause}",
            (lead_id, campaign_id, step_number - 1, *args),
        ).fetchone()
        return row["status"] if row else None


def due_sends(limit=50, org_id=None):
    clause, args = _scope(org_id, "s.org_id")
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT s.id AS send_id, s.org_id, s.campaign_id, s.step_id, s.step_number, "
            "       s.lead_id, s.scheduled_at, s.account_id, "
            "       l.email, l.business_name, l.domain, l.status AS lead_status, "
            "       l.do_not_contact, "
            "       t.subject, t.body, t.name AS template_name, "
            "       c.name AS campaign_name, c.status AS campaign_status "
            "FROM scheduled_sends s "
            "JOIN leads l            ON l.id = s.lead_id "
            "JOIN campaign_steps cs  ON cs.id = s.step_id "
            "JOIN email_templates t  ON t.id = cs.template_id "
            "JOIN campaigns c        ON c.id = s.campaign_id "
            "WHERE s.status='pending' "
            "  AND s.scheduled_at <= %s "
            "  AND c.status='active'"
            f"{clause} "
            "ORDER BY s.scheduled_at ASC LIMIT %s",
            (now(), *args, limit),
        ).fetchall()]


def claim_due_sends(limit=50, org_id=None):
    """Atomically claim due rows so another worker cannot send them too."""
    clause, args = _scope(org_id, "s.org_id")
    current = now()
    stale_before = (
        datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=30)
    ).isoformat(timespec="seconds")
    with get_conn() as c:
        claimed = c.execute(
            "WITH claimable AS ("
            "  SELECT s.id FROM scheduled_sends s "
            "  JOIN campaigns c ON c.id=s.campaign_id "
            "  WHERE s.scheduled_at <= %s AND c.status='active' "
            "    AND (s.status='pending' OR "
            "         (s.status='sending' AND "
            "          (s.claimed_at IS NULL OR s.claimed_at <= %s)))"
            f"{clause} "
            "  ORDER BY s.scheduled_at ASC "
            "  FOR UPDATE OF s SKIP LOCKED LIMIT %s"
            ") "
            "UPDATE scheduled_sends s SET status='sending', claimed_at=%s "
            "FROM claimable x WHERE s.id=x.id RETURNING s.id",
            (current, stale_before, *args, limit, current),
        ).fetchall()
        ids = [row["id"] for row in claimed]
        if not ids:
            return []
        return [dict(row) for row in c.execute(
            "SELECT s.id AS send_id, s.org_id, s.campaign_id, s.step_id, "
            "       s.step_number, s.lead_id, s.scheduled_at, s.account_id, "
            "       l.email, l.business_name, l.domain, "
            "       l.status AS lead_status, l.do_not_contact, "
            "       t.subject, t.body, t.name AS template_name, "
            "       c.name AS campaign_name, c.status AS campaign_status "
            "FROM scheduled_sends s "
            "JOIN leads l ON l.id=s.lead_id "
            "JOIN campaign_steps cs ON cs.id=s.step_id "
            "JOIN email_templates t ON t.id=cs.template_id "
            "JOIN campaigns c ON c.id=s.campaign_id "
            "WHERE s.id = ANY(%s) "
            "ORDER BY s.scheduled_at ASC",
            (ids,),
        ).fetchall()]


def release_send_claim(send_id, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            "UPDATE scheduled_sends SET status='pending', claimed_at=NULL "
            f"WHERE id=%s AND status='sending'{clause}",
            (send_id, *args),
        )


def refresh_send_claim(send_id, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            "UPDATE scheduled_sends SET claimed_at=%s "
            f"WHERE id=%s AND status='sending'{clause}",
            (now(), send_id, *args),
        )


def mark_send(send_id, status, error=None, org_id=None, account_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            "UPDATE scheduled_sends SET status=%s, sent_at=%s, error=%s, claimed_at=NULL, "
            "account_id=COALESCE(%s, account_id) "
            f"WHERE id=%s{clause}",
            (status, now() if status == "sent" else None, error, account_id,
             send_id, *args),
        )


def reschedule_send(send_id, scheduled_at, org_id=None):
    """Defer a pending send by overwriting its scheduled_at. Status stays
    'pending' so it gets re-picked up at the new time. Used by the
    business-hours gate to push outside-hours sends into the next 9am window.
    """
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            f"UPDATE scheduled_sends SET scheduled_at=%s, status='pending', claimed_at=NULL "
            f"WHERE id=%s{clause}",
            (scheduled_at, send_id, *args),
        )


def cancel_future_sends_for_lead(lead_id, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            "UPDATE scheduled_sends SET status='cancelled' "
            f"WHERE lead_id=%s AND status='pending'{clause}",
            (lead_id, *args),
        )


# ── Outreach controls (do-not-contact, pause, daily/hourly budgets) ──
# Outbound outbox + suppression
def enqueue_outbound(org_id, source, to_email, subject, body, *, lead_id=None,
                     campaign_id=None, send_id=None, thread_id=None,
                     draft_id=None, pending_reply_id=None, reply_to=None,
                     in_reply_to=None, references_hdr=None,
                     next_attempt_at=None, idempotency_key=None, note=None):
    """Insert a durable outbound intent, de-duplicated by idempotency key."""
    org = org_id_or_default(org_id)
    key = idempotency_key or f"{source}:{uuid4()}"
    when = _utc_dt(next_attempt_at) or datetime.now(timezone.utc)
    with get_conn() as c:
        row = c.execute(
            "INSERT INTO outbound_messages "
            "(org_id, source, lead_id, campaign_id, send_id, thread_id, "
            " draft_id, pending_reply_id, to_email, subject, body, reply_to, "
            " in_reply_to, references_hdr, idempotency_key, next_attempt_at, note) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (idempotency_key) DO NOTHING "
            "RETURNING id, status",
            (org, source, lead_id, campaign_id, send_id, thread_id, draft_id,
             pending_reply_id, to_email, subject, body, reply_to, in_reply_to,
             references_hdr, key, when, note),
        ).fetchone()
        if row:
            return {"id": row["id"], "created": True, "status": row["status"]}
        row = c.execute(
            "SELECT id, status FROM outbound_messages WHERE idempotency_key=%s",
            (key,),
        ).fetchone()
        return {"id": row["id"], "created": False, "status": row["status"]}


def claim_due_outbound(limit=1, org_id=None, worker_id=None):
    """Claim due outbound rows; stale in-flight rows become 'unknown'.

    A row is marked 'sending' and leased before SMTP. If the process dies after
    SMTP accepted the message but before we record success, the lease expires
    and the row is marked 'unknown' (never auto-retried) to prevent duplicates.
    """
    worker = worker_id or f"outbound:{uuid4()}"
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            "UPDATE outbound_messages SET status='unknown', locked_by=NULL, "
            "lease_expires_at=NULL, "
            "last_error=COALESCE(last_error, "
            "'lease expired after send started; not retried automatically') "
            "WHERE status='sending' AND lease_expires_at IS NOT NULL "
            "AND lease_expires_at < clock_timestamp()"
            f"{clause}",
            tuple(args),
        )
        claimed = c.execute(
            "WITH claimable AS ("
            "  SELECT id FROM outbound_messages "
            "  WHERE status IN ('pending','retry') "
            "    AND next_attempt_at <= clock_timestamp()"
            f"{clause}"
            "  ORDER BY next_attempt_at ASC, id ASC "
            "  FOR UPDATE SKIP LOCKED LIMIT %s"
            ") "
            "UPDATE outbound_messages m SET status='sending', "
            "  attempts=m.attempts + 1, locked_by=%s, "
            "  lease_expires_at=clock_timestamp() + interval '10 minutes', "
            "  last_error=NULL "
            "FROM claimable x WHERE m.id=x.id RETURNING m.id",
            (*args, limit, worker),
        ).fetchall()
        ids = [row["id"] for row in claimed]
        if not ids:
            return []
        return [dict(row) for row in c.execute(
            "SELECT m.*, l.business_name, l.status AS lead_status, "
            "       c.name AS campaign_name, s.step_number "
            "FROM outbound_messages m "
            "LEFT JOIN leads l ON l.id=m.lead_id "
            "LEFT JOIN campaigns c ON c.id=m.campaign_id "
            "LEFT JOIN scheduled_sends s ON s.id=m.send_id "
            "WHERE m.id = ANY(%s) "
            "ORDER BY m.next_attempt_at ASC, m.id ASC",
            (ids,),
        ).fetchall()]


def set_outbound_message_id(row_id, message_id):
    with get_conn() as c:
        c.execute(
            "UPDATE outbound_messages SET message_id=%s WHERE id=%s",
            (message_id, row_id),
        )


def mark_outbound_sent(row_id, account_id=None, message_id=None):
    with get_conn() as c:
        c.execute(
            "UPDATE outbound_messages SET status='sent', sent_at=clock_timestamp(), "
            "locked_by=NULL, lease_expires_at=NULL, last_error=NULL, "
            "account_id=COALESCE(%s, account_id), "
            "message_id=COALESCE(%s, message_id) WHERE id=%s",
            (account_id, message_id, row_id),
        )


def mark_outbound_retry(row_id, error, delay_seconds):
    with get_conn() as c:
        row = c.execute(
            "UPDATE outbound_messages SET "
            "status=CASE WHEN attempts >= %s THEN 'dead_letter' ELSE 'retry' END, "
            "next_attempt_at=clock_timestamp() + (%s * interval '1 second'), "
            "last_error=%s, locked_by=NULL, lease_expires_at=NULL, "
            "dead_lettered_at=CASE WHEN attempts >= %s THEN clock_timestamp() "
            "ELSE NULL END WHERE id=%s RETURNING status",
            (
                _outbound_max_attempts(),
                max(0, delay_seconds),
                (error or "")[:400],
                _outbound_max_attempts(),
                row_id,
            ),
        ).fetchone()
        if not row:
            raise RuntimeError(f"Outbound message {row_id} does not exist.")
        return row["status"]


def _outbound_max_attempts():
    raw = os.getenv("OUTBOUND_MAX_ATTEMPTS", "8")
    try:
        maximum = int(raw)
    except ValueError as exc:
        raise ValueError("OUTBOUND_MAX_ATTEMPTS must be an integer from 1 to 100.") from exc
    if not 1 <= maximum <= 100:
        raise ValueError("OUTBOUND_MAX_ATTEMPTS must be an integer from 1 to 100.")
    return maximum


def reserve_outbound_domain(org_id, outbound_id, to_email, max_per_hour=10):
    if max_per_hour <= 0:
        return False
    domain = _email_domain(to_email)
    if not domain:
        return False
    org = org_id_or_default(org_id)
    with get_conn() as c:
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"outbound-domain:{org}:{domain}",),
        )
        c.execute(
            "DELETE FROM outbound_domain_reservations "
            "WHERE org_id=%s AND domain=%s AND expires_at <= clock_timestamp()",
            (org, domain),
        )
        existing = c.execute(
            "SELECT outbound_id FROM outbound_domain_reservations "
            "WHERE outbound_id=%s AND org_id=%s",
            (outbound_id, org),
        ).fetchone()
        if existing:
            return True

        counts = c.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM outbound_messages "
            " WHERE org_id=%s AND status='sent' "
            "   AND sent_at > clock_timestamp() - interval '1 hour' "
            "   AND lower(split_part(to_email, '@', 2))=%s) AS sent, "
            "(SELECT COUNT(*) FROM outbound_domain_reservations "
            " WHERE org_id=%s AND domain=%s "
            "   AND expires_at > clock_timestamp()) AS reserved, "
            "clock_timestamp() AS db_now",
            (org, domain, org, domain),
        ).fetchone()
        if counts["sent"] + counts["reserved"] >= max_per_hour:
            next_sent = c.execute(
                "SELECT MIN(sent_at) + interval '1 hour' AS available_at "
                "FROM (SELECT sent_at FROM outbound_messages "
                "WHERE org_id=%s AND status='sent' "
                "AND sent_at > clock_timestamp() - interval '1 hour' "
                "AND lower(split_part(to_email, '@', 2))=%s "
                "ORDER BY sent_at ASC LIMIT %s) recent",
                (org, domain, max_per_hour),
            ).fetchone()["available_at"]
            next_reserved = c.execute(
                "SELECT MIN(expires_at) AS available_at "
                "FROM outbound_domain_reservations "
                "WHERE org_id=%s AND domain=%s "
                "AND expires_at > clock_timestamp()",
                (org, domain),
            ).fetchone()["available_at"]
            available_at = min(
                [value for value in (next_sent, next_reserved) if value is not None]
            )
            raise OutreachDomainRateLimited(available_at, domain)

        c.execute(
            "INSERT INTO outbound_domain_reservations "
            "(outbound_id, org_id, domain, expires_at) "
            "VALUES (%s, %s, %s, clock_timestamp() + interval '1 hour') "
            "ON CONFLICT (outbound_id) DO UPDATE SET "
            "org_id=EXCLUDED.org_id, domain=EXCLUDED.domain, "
            "expires_at=EXCLUDED.expires_at",
            (outbound_id, org, domain),
        )
    return True


def release_outbound_domain(outbound_id):
    with get_conn() as c:
        c.execute(
            "DELETE FROM outbound_domain_reservations WHERE outbound_id=%s",
            (outbound_id,),
        )


def outbound_provider_status(org_id, provider_key):
    with get_conn() as c:
        row = c.execute(
            "SELECT paused_until, pause_reason, clock_timestamp() AS db_now "
            "FROM outbound_provider_state WHERE org_id=%s AND provider_key=%s",
            (org_id_or_default(org_id), provider_key),
        ).fetchone()
    if not row:
        return {"paused_until": None, "pause_reason": None, "pause_active": False}
    return {
        "paused_until": row["paused_until"],
        "pause_reason": row["pause_reason"],
        "pause_active": row["paused_until"] > row["db_now"],
    }


def pause_outbound_provider(org_id, provider_key, seconds, reason):
    with get_conn() as c:
        row = c.execute(
            "INSERT INTO outbound_provider_state "
            "(org_id, provider_key, paused_until, pause_reason) "
            "VALUES (%s, %s, clock_timestamp() + (%s * interval '1 second'), %s) "
            "ON CONFLICT (org_id, provider_key) DO UPDATE SET "
            "paused_until=GREATEST("
            "outbound_provider_state.paused_until, "
            "clock_timestamp() + (%s * interval '1 second')), "
            "pause_reason=EXCLUDED.pause_reason, updated_at=clock_timestamp() "
            "RETURNING paused_until",
            (org_id_or_default(org_id), provider_key, seconds, reason, seconds),
        ).fetchone()
        return row["paused_until"]


def release_outbound_claim_to_retry(row_id, error, available_at):
    with get_conn() as c:
        row = c.execute(
            "UPDATE outbound_messages SET "
            "status=CASE WHEN attempts >= %s THEN 'dead_letter' ELSE 'retry' END, "
            "next_attempt_at=%s, last_error=%s, locked_by=NULL, "
            "lease_expires_at=NULL, "
            "dead_lettered_at=CASE WHEN attempts >= %s THEN clock_timestamp() "
            "ELSE NULL END WHERE id=%s RETURNING status",
            (
                _outbound_max_attempts(),
                available_at,
                (error or "")[:400],
                _outbound_max_attempts(),
                row_id,
            ),
        ).fetchone()
        if not row:
            raise RuntimeError(f"Outbound message {row_id} does not exist.")
        return row["status"]


def mark_outbound_failed(row_id, status, error, account_id=None):
    with get_conn() as c:
        c.execute(
            "UPDATE outbound_messages SET status=%s, last_error=%s, "
            "locked_by=NULL, lease_expires_at=NULL, "
            "account_id=COALESCE(%s, account_id) WHERE id=%s",
            (status, (error or "")[:400], account_id, row_id),
        )


def release_outbound_claim(row_id):
    with get_conn() as c:
        c.execute(
            "UPDATE outbound_messages SET status='pending', "
            "next_attempt_at=clock_timestamp(), locked_by=NULL, "
            "lease_expires_at=NULL WHERE id=%s AND status='sending'",
            (row_id,),
        )


def outbound_queue_summary(org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        rows = c.execute(
            f"SELECT status, COUNT(*) AS n FROM outbound_messages WHERE 1=1{clause} "
            "GROUP BY status",
            tuple(args),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def get_imap_cursor(org_id, source_key, mailbox, uid_validity):
    org = org_id_or_default(org_id)
    with get_conn() as c:
        row = c.execute(
            "SELECT uid_validity, last_uid FROM imap_cursors "
            "WHERE org_id=%s AND source_key=%s AND mailbox=%s FOR UPDATE",
            (org, source_key, mailbox),
        ).fetchone()
        if not row:
            c.execute(
                "INSERT INTO imap_cursors "
                "(org_id, source_key, mailbox, uid_validity, last_uid) "
                "VALUES (%s, %s, %s, %s, 0) ON CONFLICT DO NOTHING",
                (org, source_key, mailbox, uid_validity),
            )
            return 0
        if row["uid_validity"] != uid_validity:
            c.execute(
                "UPDATE imap_cursors SET uid_validity=%s, last_uid=0, "
                "updated_at=clock_timestamp() "
                "WHERE org_id=%s AND source_key=%s AND mailbox=%s",
                (uid_validity, org, source_key, mailbox),
            )
            return 0
        return row["last_uid"]


def persist_inbound_message(org_id, source_key, mailbox, uid_validity, uid, raw_message):
    org = org_id_or_default(org_id)
    with get_conn() as c:
        row = c.execute(
            "INSERT INTO inbound_messages "
            "(org_id, source_key, mailbox, uid_validity, uid, raw_message) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (org_id, source_key, mailbox, uid_validity, uid) "
            "DO UPDATE SET raw_message=EXCLUDED.raw_message "
            "RETURNING id",
            (org, source_key, mailbox, uid_validity, uid, raw_message),
        ).fetchone()
        return row["id"]


def advance_imap_cursor(org_id, source_key, mailbox, uid_validity, last_uid):
    org = org_id_or_default(org_id)
    with get_conn() as c:
        c.execute(
            "INSERT INTO imap_cursors "
            "(org_id, source_key, mailbox, uid_validity, last_uid) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (org_id, source_key, mailbox) DO UPDATE SET "
            "uid_validity=EXCLUDED.uid_validity, "
            "last_uid=CASE WHEN imap_cursors.uid_validity=EXCLUDED.uid_validity "
            "THEN GREATEST(imap_cursors.last_uid, EXCLUDED.last_uid) "
            "ELSE EXCLUDED.last_uid END, updated_at=clock_timestamp()",
            (org, source_key, mailbox, uid_validity, last_uid),
        )


def claim_inbound_messages(org_id, source_key, mailbox, limit=50, worker_id=None):
    org = org_id_or_default(org_id)
    worker = worker_id or f"inbound:{uuid4()}"
    with get_conn() as c:
        c.execute(
            "UPDATE inbound_messages SET "
            "status=CASE WHEN attempts >= 10 THEN 'dead_letter' ELSE 'retry' END, "
            "locked_by=NULL, lease_expires_at=NULL, "
            "last_error=COALESCE(last_error, 'inbound processing lease expired') "
            "WHERE status='processing' AND lease_expires_at < clock_timestamp() "
            "AND org_id=%s AND source_key=%s AND mailbox=%s",
            (org, source_key, mailbox),
        )
        return [dict(row) for row in c.execute(
            "WITH ready AS ("
            "  SELECT id FROM inbound_messages "
            "  WHERE org_id=%s AND source_key=%s AND mailbox=%s "
            "    AND status IN ('pending', 'retry') "
            "  ORDER BY id ASC FOR UPDATE SKIP LOCKED LIMIT %s"
            ") "
            "UPDATE inbound_messages m SET status='processing', "
            "  attempts=m.attempts+1, locked_by=%s, "
            "  lease_expires_at=clock_timestamp()+interval '10 minutes', "
            "  last_error=NULL FROM ready r WHERE m.id=r.id "
            "RETURNING m.*",
            (org, source_key, mailbox, limit, worker),
        ).fetchall()]


def mark_inbound_message(row_id, status, error=None):
    if status not in ("processed", "ignored", "retry", "dead_letter"):
        raise ValueError(f"Unsupported inbound message status: {status}")
    with get_conn() as c:
        if status == "retry":
            row = c.execute(
                "UPDATE inbound_messages SET "
                "status=CASE WHEN attempts >= 10 THEN 'dead_letter' ELSE 'retry' END, "
                "locked_by=NULL, lease_expires_at=NULL, last_error=%s "
                "WHERE id=%s RETURNING status",
                ((error or "")[:500], row_id),
            ).fetchone()
            if not row:
                raise RuntimeError(f"Inbound message {row_id} does not exist.")
            return row["status"]
        row = c.execute(
            "UPDATE inbound_messages SET status=%s, locked_by=NULL, "
            "lease_expires_at=NULL, last_error=%s, "
            "processed_at=clock_timestamp() WHERE id=%s RETURNING id",
            (status, (error or "")[:500], row_id),
        ).fetchone()
        if not row:
            raise RuntimeError(f"Inbound message {row_id} does not exist.")
        return status


def suppression_reason(org_id, email, lead_id=None):
    """Return why this recipient must not be emailed, or None if allowed."""
    org = org_id_or_default(org_id)
    normalized = _normalize_email(email)
    domain = _email_domain(normalized)
    with get_conn() as c:
        row = c.execute(
            "SELECT reason FROM suppression_list WHERE org_id=%s "
            "AND (lower(email)=%s OR (domain IS NOT NULL AND lower(domain)=%s)) "
            "ORDER BY (lower(email)=%s) DESC LIMIT 1",
            (org, normalized, domain, normalized),
        ).fetchone()
        if row:
            return row["reason"] or "suppressed"
        if lead_id:
            lead = c.execute(
                "SELECT do_not_contact, status FROM leads "
                "WHERE id=%s AND org_id=%s",
                (lead_id, org),
            ).fetchone()
            if lead:
                if lead["do_not_contact"]:
                    return "do_not_contact"
                if lead["status"] in ("replied", "converted", "dead"):
                    return f"lead status: {lead['status']}"
    return None


def add_suppression(org_id, email, reason="unsubscribe", source=None):
    org = org_id_or_default(org_id)
    normalized = _normalize_email(email)
    if not normalized:
        return False
    with get_conn() as c:
        c.execute(
            "INSERT INTO suppression_list (org_id, email, domain, reason, source) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (org_id, email) DO UPDATE SET "
            "reason=EXCLUDED.reason, source=EXCLUDED.source",
            (org, normalized, _email_domain(normalized), reason, source),
        )
    return True


def set_leads_do_not_contact(lead_ids, value=True, org_id=None):
    """Mark one or more leads as do-not-contact (or clear the flag)."""
    if not lead_ids:
        return 0
    placeholders = ",".join(["%s"] * len(lead_ids))
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        cur = c.execute(
            f"UPDATE leads SET do_not_contact=%s WHERE id IN ({placeholders}){clause}",
            [bool(value), *lead_ids, *args],
        )
        return cur.rowcount


def get_setting(key, default=None, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            f"SELECT value FROM outreach_settings WHERE key=%s{clause}",
            (key, *args),
        ).fetchone()
        return row["value"] if row else default


def set_setting(key, value, org_id=None):
    org = org_id_or_default(org_id)
    with get_conn() as c:
        c.execute(
            "INSERT INTO outreach_settings (org_id, key, value) VALUES (%s, %s, %s) "
            "ON CONFLICT (org_id, key) DO UPDATE SET value=EXCLUDED.value",
            (org, key, str(value)),
        )


class OutreachDispatchPaused(Exception):
    def __init__(self, paused_until, reason):
        self.paused_until = paused_until
        self.reason = reason
        super().__init__(f"Outreach dispatch is paused until {paused_until.isoformat()}")


class OutboundProviderPaused(Exception):
    def __init__(self, paused_until, reason):
        self.paused_until = paused_until
        self.reason = reason
        super().__init__(f"SMTP provider paused until {paused_until.isoformat()}: {reason}")


class OutreachDomainRateLimited(Exception):
    def __init__(self, available_at, domain):
        self.available_at = available_at
        self.domain = domain
        super().__init__(
            f"Recipient domain {domain} is at its hourly send limit until "
            f"{available_at.isoformat()}"
        )


def _extend_outreach_pause(c, org_id, seconds, reason):
    c.execute(
        "INSERT INTO outreach_dispatch_state (org_id, paused_until, pause_reason) "
        "VALUES (%s, clock_timestamp() + (%s * interval '1 second'), %s) "
        "ON CONFLICT (org_id) DO UPDATE SET "
        "paused_until=GREATEST("
        "COALESCE(outreach_dispatch_state.paused_until, clock_timestamp()), "
        "clock_timestamp() + (%s * interval '1 second')), "
        "pause_reason=EXCLUDED.pause_reason",
        (org_id, seconds, reason, seconds),
    )
    return c.execute(
        "SELECT paused_until FROM outreach_dispatch_state WHERE org_id=%s",
        (org_id,),
    ).fetchone()["paused_until"]


def get_outreach_rate(org_id=None):
    org = org_id_or_default(org_id)
    value = get_setting("max_emails_per_minute", "60", org_id=org)
    try:
        rate = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Stored outreach rate must be an integer.") from exc
    if not 1 <= rate <= 60:
        raise RuntimeError("Stored outreach rate must be between 1 and 60.")
    return rate


def get_send_jitter_max_seconds(org_id=None):
    """Maximum random delay (seconds) inserted between consecutive sends."""
    org = org_id_or_default(org_id)
    value = get_setting("send_jitter_max_seconds", "0", org_id=org)
    try:
        seconds = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Stored send jitter must be an integer.") from exc
    if not 0 <= seconds <= 3600:
        raise RuntimeError("Stored send jitter must be between 0 and 3600 seconds.")
    return seconds


def set_send_jitter_max_seconds(org_id, seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 0 <= seconds <= 3600:
        raise ValueError("send_jitter_max_seconds must be an integer between 0 and 3600.")
    set_setting("send_jitter_max_seconds", seconds, org_id=org_id)


def get_send_max_per_domain_per_hour(org_id=None):
    org = org_id_or_default(org_id)
    default = os.getenv("SEND_MAX_PER_DOMAIN_PER_HOUR", "10")
    value = get_setting("send_max_per_domain_per_hour", default, org_id=org)
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            "Stored per-domain send limit must be an integer."
        ) from exc
    if not 1 <= limit <= 10000:
        raise RuntimeError(
            "Stored per-domain send limit must be between 1 and 10000."
        )
    return limit


def set_send_max_per_domain_per_hour(org_id, limit):
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 10000
    ):
        raise ValueError(
            "send_max_per_domain_per_hour must be an integer between 1 and 10000."
        )
    set_setting("send_max_per_domain_per_hour", limit, org_id=org_id)


def get_hourly_allocation_limit(org_id=None):
    with get_conn() as c:
        row = c.execute(
            "SELECT hourly_allocation_limit FROM organizations WHERE id=%s",
            (org_id_or_default(org_id),),
        ).fetchone()
    if not row:
        raise RuntimeError("Organization not found when reading its hourly allocation limit.")
    limit = row["hourly_allocation_limit"]
    if not 1 <= limit <= 3600:
        raise RuntimeError("Stored hourly allocation limit must be between 1 and 3600.")
    return limit


def set_hourly_allocation_limit(org_id, limit):
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 3600:
        raise ValueError("hourly_allocation_limit must be an integer between 1 and 3600.")
    with get_conn() as c:
        result = c.execute(
            "UPDATE organizations SET hourly_allocation_limit=%s WHERE id=%s",
            (limit, org_id_or_default(org_id)),
        )
        if result.rowcount != 1:
            raise RuntimeError("Organization not found when saving its hourly allocation limit.")


def outreach_slot_interval(hourly_limit):
    if isinstance(hourly_limit, bool) or not isinstance(hourly_limit, int):
        raise ValueError("hourly_limit must be an integer.")
    if not 1 <= hourly_limit <= 3600:
        raise ValueError("hourly_limit must be between 1 and 3600.")
    return 3600.0 / hourly_limit


_OUTREACH_SLOT_LEAD_SECONDS = 10


def select_outreach_slot(db_now, hourly_limit, allocation_hour=None, allocated_count=0):
    """Choose the next unused fixed window using a PostgreSQL timestamp."""
    interval_seconds = outreach_slot_interval(hourly_limit)
    hour_start = db_now.replace(minute=0, second=0, microsecond=0)
    if allocation_hour and allocation_hour > hour_start:
        slot_hour = allocation_hour
        slot_index = allocated_count
        if slot_index >= hourly_limit:
            slot_hour += timedelta(hours=1)
            slot_index = 0
        slot_start = slot_hour + timedelta(seconds=slot_index * interval_seconds)
        slot_end = slot_start + timedelta(seconds=interval_seconds)
        return slot_hour, slot_index, slot_start, slot_end
    if allocation_hour != hour_start:
        allocated_count = 0
    elapsed = (db_now - hour_start).total_seconds()
    current_slot = min(hourly_limit, math.ceil(elapsed / interval_seconds))
    slot_index = max(current_slot, allocated_count)
    slot_hour = hour_start
    if slot_index >= hourly_limit:
        slot_hour += timedelta(hours=1)
        slot_index = 0
    slot_start = slot_hour + timedelta(seconds=slot_index * interval_seconds)
    slot_end = slot_start + timedelta(seconds=interval_seconds)
    return slot_hour, slot_index, slot_start, slot_end


def outreach_dispatch_status(org_id=None):
    with get_conn() as c:
        row = c.execute(
            "SELECT s.paused_until, s.pause_reason, clock_timestamp() AS db_now "
            "FROM outreach_dispatch_state s WHERE s.org_id=%s",
            (org_id_or_default(org_id),),
        ).fetchone()
    pause_until = row["paused_until"] if row else None
    reason = row["pause_reason"] if row else None
    return {
        "paused_until": pause_until.isoformat() if pause_until else None,
        "pause_reason": reason,
        "pause_active": bool(pause_until and pause_until > row["db_now"]),
    }


@contextmanager
def outreach_dispatch_slot(org_id, hourly_limit, claimed_send_id=None):
    """Reserve and wait for one fixed slot in the database-clock hour."""
    org = org_id_or_default(org_id)
    reservation_started = time.monotonic()
    slot_start, slot_end, db_now = _reserve_outreach_slot(org, hourly_limit)
    reservation_db_now = db_now

    slot_is_current = False
    while True:
        elapsed = time.monotonic() - reservation_started
        estimated_db_now = reservation_db_now + timedelta(seconds=elapsed)
        if estimated_db_now >= slot_end:
            break
        delay = (slot_start - estimated_db_now).total_seconds()
        if delay <= 0:
            slot_is_current = True
            break
        time.sleep(min(delay, 30.0))
        if claimed_send_id is not None and delay > 30.0:
            refresh_send_claim(claimed_send_id, org_id=org)

    if not slot_is_current:
        raise OutreachDispatchBusy(
            "The reserved dispatch slot expired before it could be used."
        )

    yield slot_start


def _reserve_outreach_slot(org, hourly_limit):
    """Reserve a tenant's next fixed slot in one short database transaction."""
    if (
        isinstance(hourly_limit, bool)
        or not isinstance(hourly_limit, int)
        or not 1 <= hourly_limit <= 3600
    ):
        raise ValueError("hourly_limit must be between 1 and 3600.")
    with get_conn() as c:
        c.execute("SET LOCAL lock_timeout = '10s'")
        try:
            c.execute(
                "INSERT INTO outreach_dispatch_state (org_id) VALUES (%s) "
                "ON CONFLICT (org_id) DO NOTHING",
                (org,),
            )
            state = c.execute(
                "WITH clock AS (SELECT clock_timestamp() AS db_now) "
                "SELECT s.paused_until, s.pause_reason, s.allocation_hour, "
                "s.allocated_count, s.last_slot_index, clock.db_now, "
                "date_trunc('hour', clock.db_now AT TIME ZONE 'UTC') "
                "AT TIME ZONE 'UTC' AS hour_start "
                "FROM outreach_dispatch_state s CROSS JOIN clock "
                "WHERE s.org_id=%s FOR UPDATE OF s",
                (org,),
            ).fetchone()
        except pg_errors.LockNotAvailable as exc:
            raise OutreachDispatchBusy(
                "Timed out waiting to reserve the tenant's next dispatch slot."
            ) from exc
        if state["paused_until"] and state["paused_until"] > state["db_now"]:
            raise OutreachDispatchPaused(
                state["paused_until"], state["pause_reason"]
            )

        hour_start = state["hour_start"]
        reservation_time = state["db_now"] + timedelta(
            seconds=_OUTREACH_SLOT_LEAD_SECONDS
        )
        reservation_hour = reservation_time.replace(
            minute=0, second=0, microsecond=0
        )
        sent_this_hour = c.execute(
            "SELECT COUNT(*) AS count FROM email_events "
            "WHERE org_id=%s AND event_type IN ('sent', 'booking_sent') "
            "AND created_at >= %s",
            (
                org,
                hour_start.replace(tzinfo=None).isoformat(timespec="seconds"),
            ),
        ).fetchone()["count"]
        same_hour = state["allocation_hour"] == reservation_hour
        future_hour = (
            state["allocation_hour"]
            and state["allocation_hour"] > reservation_hour
        )
        allocated_count = max(
            state["allocated_count"] if same_hour or future_hour else 0,
            (
                state["last_slot_index"] + 1
                if same_hour and state["last_slot_index"] is not None
                else 0
            ),
            (
            sent_this_hour
            if reservation_hour == hour_start and not future_hour
            else 0
            ),
        )
        slot_hour, slot_index, slot_start, slot_end = select_outreach_slot(
            reservation_time,
            hourly_limit,
            allocation_hour=state["allocation_hour"],
            allocated_count=allocated_count,
        )
        c.execute(
            "UPDATE outreach_dispatch_state SET allocation_hour=%s, "
            "last_slot_index=%s, allocated_count=%s, next_send_at=%s "
            "WHERE org_id=%s",
            (
                slot_hour,
                slot_index,
                slot_index + 1,
                slot_start,
                org,
            ),
        )
    return slot_start, slot_end, state["db_now"]


def is_outreach_paused(org_id=None):
    """Per-org kill-switch for the send pipeline (toggle via /api/system/pause)."""
    return get_setting("outreach_paused", org_id=org_id) in ("1", "true", "True")


# ── Email accounts (multiple SMTP/IMAP senders per org) ──────
_ACCOUNT_FIELDS = {
    "label", "from_name", "from_email", "reply_to",
    "smtp_host", "smtp_port", "smtp_user", "smtp_use_tls",
    "imap_host", "imap_port", "imap_user", "imap_mailbox",
    "daily_limit", "hourly_limit", "warmup_started_at", "warmup_days",
    "status",
}
_ACCOUNT_SECRETS = {
    "smtp_password": "smtp_password_enc",
    "imap_password": "imap_password_enc",
}


def _account_public(row, decrypt=False):
    from credentials import decrypt_secret

    d = dict(row)
    d["has_smtp_password"] = bool(d.pop("smtp_password_enc", None))
    d["has_imap_password"] = bool(d.pop("imap_password_enc", None))
    if decrypt:
        d["smtp_password"] = decrypt_secret(row.get("smtp_password_enc"))
        d["imap_password"] = decrypt_secret(row.get("imap_password_enc"))
    return d


def create_email_account(org_id, from_email, smtp_host, *, smtp_port=587,
                         smtp_user=None, smtp_password=None, from_name=None,
                         reply_to=None, smtp_use_tls=True, imap_host=None,
                         imap_port=993, imap_user=None, imap_password=None,
                         imap_mailbox="INBOX", label=None, daily_limit=50,
                         hourly_limit=10, warmup_days=0, status="active"):
    from credentials import encrypt_secret

    org = org_id_or_default(org_id)
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO email_accounts "
            "(org_id, label, from_name, from_email, reply_to, smtp_host, "
            " smtp_port, smtp_user, smtp_password_enc, smtp_use_tls, "
            " imap_host, imap_port, imap_user, imap_password_enc, imap_mailbox, "
            " daily_limit, hourly_limit, warmup_started_at, warmup_days, "
            " status, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "RETURNING id",
            (org, label, from_name, from_email, reply_to, smtp_host,
             smtp_port, smtp_user, encrypt_secret(smtp_password), smtp_use_tls,
             imap_host, imap_port, imap_user, encrypt_secret(imap_password),
             imap_mailbox, daily_limit, hourly_limit,
             now() if warmup_days else None, warmup_days, status, now()),
        )
        return cur.fetchone()["id"]


def list_email_accounts(org_id=None, include_deleted=False, decrypt=False):
    clause, args = _scope(org_id, "org_id")
    status_clause = "" if include_deleted else " AND status <> 'deleted'"
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM email_accounts WHERE 1=1"
            f"{clause}{status_clause} ORDER BY id ASC",
            tuple(args),
        ).fetchall()
        return [_account_public(r, decrypt=decrypt) for r in rows]


def get_email_account(account_id, org_id=None, decrypt=True):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            f"SELECT * FROM email_accounts WHERE id=%s{clause}",
            (account_id, *args),
        ).fetchone()
        return _account_public(row, decrypt=decrypt) if row else None


def update_email_account(account_id, org_id=None, **fields):
    from credentials import encrypt_secret

    sets, values = [], []
    for key, value in fields.items():
        if key in _ACCOUNT_FIELDS:
            sets.append(f"{key}=%s")
            values.append(value)
        elif key in _ACCOUNT_SECRETS:
            sets.append(f"{_ACCOUNT_SECRETS[key]}=%s")
            values.append(encrypt_secret(value) if value else None)
    if not sets:
        return False
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        cur = c.execute(
            "UPDATE email_accounts SET " + ", ".join(sets) +
            f" WHERE id=%s{clause}",
            (*values, account_id, *args),
        )
        return cur.rowcount > 0


def delete_email_account(account_id, org_id=None):
    """Soft-delete — keeps historical events' account_id resolvable."""
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        cur = c.execute(
            f"UPDATE email_accounts SET status='deleted' WHERE id=%s{clause}",
            (account_id, *args),
        )
        return cur.rowcount > 0


def touch_email_account(account_id):
    with get_conn() as c:
        c.execute(
            "UPDATE email_accounts SET last_used_at=%s WHERE id=%s",
            (now(), account_id),
        )


def list_imap_accounts():
    """All active accounts with usable IMAP creds, across every org."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT * FROM email_accounts "
            "WHERE status='active' AND imap_host IS NOT NULL "
            "AND imap_user IS NOT NULL"
        ).fetchall()
        return [_account_public(r, decrypt=True) for r in rows]


def account_sent_since(account_id, start_iso):
    with get_conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM email_events "
            "WHERE account_id=%s AND event_type IN ('sent', 'booking_sent') "
            "AND created_at >= %s",
            (account_id, start_iso),
        ).fetchone()
        return row["n"] if row else 0


def reserve_email_account(org_id, reservation_key, lead_id=None,
                          preferred_account_id=None):
    """Atomically select and reserve an account with daily/hourly headroom."""
    from credentials import decrypt_secret

    day_start, hour_start = _day_start_iso(), _hour_start_iso()
    current = now()
    expires = (
        datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10)
    ).isoformat(timespec="seconds")
    with get_conn() as c:
        c.execute(
            "DELETE FROM email_account_reservations "
            "WHERE org_id=%s AND expires_at <= %s",
            (org_id, current),
        )
        existing = c.execute(
            "SELECT a.* FROM email_account_reservations r "
            "JOIN email_accounts a ON a.id=r.account_id "
            "WHERE r.reservation_key=%s AND r.org_id=%s "
            "AND r.expires_at > %s AND a.status='active'",
            (reservation_key, org_id, current),
        ).fetchone()
        if existing:
            row = dict(existing)
            row["smtp_password"] = decrypt_secret(row.get("smtp_password_enc"))
            row["imap_password"] = decrypt_secret(row.get("imap_password_enc"))
            row["has_smtp_password"] = bool(row.pop("smtp_password_enc", None))
            row["has_imap_password"] = bool(row.pop("imap_password_enc", None))
            return row

        accounts = c.execute(
            "SELECT * FROM email_accounts "
            "WHERE org_id=%s AND status='active' ORDER BY id FOR UPDATE",
            (org_id,),
        ).fetchall()
        if not accounts:
            return None

        sticky_id = preferred_account_id
        if not sticky_id and lead_id:
            sticky = c.execute(
                "SELECT account_id FROM email_events "
                "WHERE lead_id=%s AND org_id=%s "
                "AND event_type IN ('sent', 'booking_sent') AND account_id IS NOT NULL "
                "ORDER BY id DESC LIMIT 1",
                (lead_id, org_id),
            ).fetchone()
            sticky_id = sticky["account_id"] if sticky else None

        candidates = []
        for account in accounts:
            sent = c.execute(
                "SELECT "
                "COUNT(*) FILTER (WHERE created_at >= %s) AS today, "
                "COUNT(*) FILTER (WHERE created_at >= %s) AS this_hour "
                "FROM email_events WHERE account_id=%s "
                "AND event_type IN ('sent', 'booking_sent') "
                "AND created_at >= %s",
                (day_start, hour_start, account["id"], day_start),
            ).fetchone()
            reserved = c.execute(
                "SELECT "
                "COUNT(*) FILTER (WHERE created_at >= %s) AS today, "
                "COUNT(*) FILTER (WHERE created_at >= %s) AS this_hour "
                "FROM email_account_reservations "
                "WHERE account_id=%s AND expires_at > %s",
                (day_start, hour_start, account["id"], current),
            ).fetchone()
            used_today = sent["today"] + reserved["today"]
            used_hour = sent["this_hour"] + reserved["this_hour"]
            daily_limit = int(account.get("daily_limit") or 0)
            hourly_limit = int(account.get("hourly_limit") or 0)
            if daily_limit and used_today >= _warmup_cap(dict(account)):
                continue
            if hourly_limit and used_hour >= hourly_limit:
                continue
            candidates.append((account, used_today, account["id"] == sticky_id))

        if not candidates:
            return None
        candidates.sort(key=lambda item: (not item[2], item[1], item[0]["id"]))
        account = candidates[0][0]
        c.execute(
            "INSERT INTO email_account_reservations "
            "(reservation_key, org_id, account_id, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (reservation_key) DO NOTHING",
            (reservation_key, org_id, account["id"], current, expires),
        )
        row = dict(account)
        row["smtp_password"] = decrypt_secret(row.get("smtp_password_enc"))
        row["imap_password"] = decrypt_secret(row.get("imap_password_enc"))
        row["has_smtp_password"] = bool(row.pop("smtp_password_enc", None))
        row["has_imap_password"] = bool(row.pop("imap_password_enc", None))
        return row


def release_email_account_reservation(reservation_key):
    if not reservation_key:
        return
    with get_conn() as c:
        c.execute(
            "DELETE FROM email_account_reservations WHERE reservation_key=%s",
            (reservation_key,),
        )


def _warmup_cap(acct):
    limit = int(acct.get("daily_limit") or 0)
    days = int(acct.get("warmup_days") or 0)
    started = acct.get("warmup_started_at")
    if not limit or not days or not started:
        return limit
    try:
        elapsed = max(1, (datetime.now() - datetime.fromisoformat(started)).days + 1)
    except (TypeError, ValueError):
        return limit
    if elapsed >= days:
        return limit
    ramp = max(5, round(limit * elapsed / days))
    return min(limit, ramp)


def pick_email_account(org_id, lead_id=None):
    """Pick the active account for a send.

    Sticky first (last account that emailed this lead), then least-used today
    with remaining daily/hourly headroom. Returns a decrypted dict or None.
    """
    accounts = list_email_accounts(org_id=org_id, decrypt=True)
    accounts = [a for a in accounts if a.get("status") == "active"]
    if not accounts:
        return None

    if lead_id:
        last_id = last_sender_for_lead(lead_id, org_id=org_id)
        for acct in accounts:
            if acct["id"] == last_id:
                if _has_capacity(acct):
                    return acct
                break

    best = None
    for acct in accounts:
        if not _has_capacity(acct):
            continue
        used = int(acct.get("_sent_today") or 0)
        if best is None or used < int(best.get("_sent_today") or 0):
            best = acct
    return best


def _has_capacity(acct):
    daily = int(acct.get("daily_limit") or 0)
    hourly = int(acct.get("hourly_limit") or 0)
    today = account_sent_since(acct["id"], _day_start_iso())
    hour = account_sent_since(acct["id"], _hour_start_iso())
    acct["_sent_today"] = today
    acct["_sent_hour"] = hour
    if daily and today >= _warmup_cap(acct):
        return False
    if hourly and hour >= hourly:
        return False
    return True


def last_sender_for_lead(lead_id, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            "SELECT account_id FROM email_events "
            "WHERE lead_id=%s AND event_type IN ('sent', 'booking_sent') "
            "AND account_id IS NOT NULL"
            f"{clause} ORDER BY id DESC LIMIT 1",
            (lead_id, *args),
        ).fetchone()
        return row["account_id"] if row else None


def last_sender_for_thread(thread_id, org_id=None):
    clause, args = _scope(org_id, "t.org_id")
    with get_conn() as c:
        row = c.execute(
            "SELECT e.account_id FROM email_events e "
            "JOIN vendor_threads t ON t.lead_id=e.lead_id "
            "WHERE t.id=%s AND e.event_type IN ('sent', 'booking_sent') "
            "AND e.account_id IS NOT NULL"
            f"{clause} ORDER BY e.id DESC LIMIT 1",
            (thread_id, *args),
        ).fetchone()
        return row["account_id"] if row else None


def count_sent_since(start_iso, org_id=None):
    """Number of emails actually sent at-or-after `start_iso`.

    Used to enforce SEND_MAX_PER_DAY / SEND_MAX_PER_HOUR budgets across both
    the worker's background runs and manual API triggers.
    """
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM email_events "
            f"WHERE event_type IN ('sent', 'booking_sent') AND created_at >= %s{clause}",
            (start_iso, *args),
        ).fetchone()
        return row["n"] if row else 0


def push_pending_sends_for_lead(lead_id, days, org_id=None):
    """Shift every pending send for a lead forward by `days`, preserving the
    gap between steps. Used when a reply signals bad timing rather than
    disinterest — pauses the sequence instead of cancelling it outright.
    """
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, scheduled_at FROM scheduled_sends "
            f"WHERE lead_id=%s AND status='pending'{clause}",
            (lead_id, *args),
        ).fetchall()
        for row in rows:
            current = datetime.fromisoformat(row["scheduled_at"])
            new_at = (current + timedelta(days=days)).isoformat(timespec="seconds")
            c.execute(
                "UPDATE scheduled_sends SET scheduled_at=%s WHERE id=%s",
                (new_at, row["id"]),
            )


def send_queue_summary(org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        rows = c.execute(
            f"SELECT status, COUNT(*) AS n FROM scheduled_sends WHERE 1=1{clause} GROUP BY status",
            tuple(args),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def list_sends(status=None, limit=100, offset=0, org_id=None):
    """Per-lead send rows (who, which step, what happened, why) — the detail
    behind send_queue_summary()'s aggregate counts, for a 'which leads did we
    actually email' view instead of querying Postgres directly. Returns
    (rows, total) so the caller can paginate instead of dumping everything
    onto one page.
    """
    org_clause, org_args = _scope(org_id, "s.org_id")
    with get_conn() as c:
        where = "WHERE s.status=%s " if status else "WHERE 1=1 "
        count_params = [status] if status else []
        where += org_clause
        count_params.extend(org_args)
        total = c.execute(
            f"SELECT COUNT(*) AS n FROM scheduled_sends s {where}", count_params
        ).fetchone()["n"]

        query = (
            "SELECT s.id, s.status, s.step_number, s.scheduled_at, s.sent_at, s.error, "
            "       l.id AS lead_id, l.business_name, l.email, "
            "       cm.name AS campaign_name "
            "FROM scheduled_sends s "
            "JOIN leads l     ON l.id = s.lead_id "
            "JOIN campaigns cm ON cm.id = s.campaign_id "
        )
        params = []
        if status:
            query += "WHERE s.status=%s "
            params.append(status)
        else:
            query += "WHERE 1=1 "
        if org_clause:
            query += org_clause
            params.extend(org_args)
        query += " "
        # 'pending' rows have no sent_at and their scheduled_at is often weeks
        # in the future (later campaign steps) — sorting those together with
        # completed rows by COALESCE(sent_at, scheduled_at) DESC let unsent,
        # future-dated rows bury real sent/failed history at the top. Instead:
        # resolved rows (sent/failed/skipped) first, most recent activity on
        # top; pending rows after that, soonest-due first.
        query += (
            "ORDER BY (s.status = 'pending'), "
            "         CASE WHEN s.status <> 'pending' THEN COALESCE(s.sent_at, s.scheduled_at) END DESC, "
            "         CASE WHEN s.status = 'pending' THEN s.scheduled_at END ASC "
            "LIMIT %s OFFSET %s"
        )
        params.extend([limit, offset])
        rows = [dict(r) for r in c.execute(query, tuple(params)).fetchall()]
        return rows, total


# ── Calls ────────────────────────────────────────────────────
def queue_call(lead_id: int, org_id=None) -> int:
    with get_conn() as c:
        org = org_id or _lead_org(c, lead_id)
        cur = c.execute(
            "INSERT INTO calls (org_id, lead_id, status, scheduled_at, created_at) "
            "VALUES (%s, %s, 'queued', %s, %s) RETURNING id",
            (org_id_or_default(org), lead_id, now(), now()),
        )
        return cur.fetchone()["id"]


def get_queued_calls(limit: int = 5, org_id=None) -> list:
    clause, args = _scope(org_id, "c.org_id")
    with get_conn() as c:
        rows = c.execute(
            """SELECT c.id, c.lead_id, c.scheduled_at,
                      l.business_name, l.phone, l.city, l.country, l.website,
                      l.improvement_note, l.homepage_snippet, l.rating,
                      l.reviews, l.last_review_days
               FROM calls c JOIN leads l ON l.id = c.lead_id
               WHERE c.status = 'queued' AND c.scheduled_at <= %s""" + clause +
               " ORDER BY c.scheduled_at ASC LIMIT %s",
            (now(), *args, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def update_call(call_id: int, org_id=None, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=%s" for k in fields)
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(
            f"UPDATE calls SET {cols} WHERE id=%s{clause}",
            (*fields.values(), call_id, *args),
        )


def update_call_by_vapi_id(vapi_call_id: str, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=%s" for k in fields)
    with get_conn() as c:
        c.execute(f"UPDATE calls SET {cols} WHERE vapi_call_id=%s", (*fields.values(), vapi_call_id))


def get_call_lead_id(vapi_call_id: str):
    with get_conn() as c:
        row = c.execute(
            "SELECT lead_id FROM calls WHERE vapi_call_id=%s", (vapi_call_id,)
        ).fetchone()
        return row["lead_id"] if row else None


def list_calls(limit: int = 100, offset: int = 0, org_id=None) -> list:
    clause, args = _scope(org_id, "c.org_id")
    with get_conn() as c:
        rows = c.execute(
            """SELECT c.*, l.business_name, l.phone, l.city, l.country, l.rating, l.reviews
               FROM calls c JOIN leads l ON l.id = c.lead_id
               WHERE 1=1""" + clause +
            " ORDER BY c.id DESC LIMIT %s OFFSET %s",
            (*args, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def calls_summary(org_id=None) -> dict:
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        rows = c.execute(
            f"SELECT status, COUNT(*) AS n FROM calls WHERE 1=1{clause} GROUP BY status",
            tuple(args),
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}


def worker_heartbeat_write(pid: int, started_at: str, jobs: dict):
    """Upsert worker state into DB so the API can read it cross-container.

    The reply-path jobs now run in a separate worker process, so merge by job
    name instead of overwriting — otherwise the two processes clobber each
    other's heartbeat. Row-locked so concurrent writers don't lose updates.
    """
    with get_conn() as c:
        row = c.execute(
            "SELECT pid, jobs FROM worker_heartbeat WHERE id=1 FOR UPDATE"
        ).fetchone()
        merged: dict = {}
        if row:
            try:
                merged = json.loads(row["jobs"] or "{}")
            except Exception:
                merged = {}
        for k, v in (jobs or {}).items():
            merged[k] = v
        c.execute(
            """INSERT INTO worker_heartbeat (id, pid, started_at, updated_at, jobs)
               VALUES (1, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE
               SET pid=%s, started_at=%s, updated_at=%s, jobs=%s""",
            (pid, started_at, now(), json.dumps(merged),
             pid, started_at, now(), json.dumps(merged)),
        )


def worker_heartbeat_read() -> dict:
    """Read worker state from DB. Returns {} if no heartbeat recorded yet."""
    try:
        with get_conn() as c:
            row = c.execute(
                "SELECT pid, started_at, updated_at, jobs FROM worker_heartbeat WHERE id=1"
            ).fetchone()
            if not row:
                return {}
            result = dict(row)
            result["jobs"] = json.loads(result.get("jobs") or "{}")
            return result
    except Exception:
        return {}


# ── Events ───────────────────────────────────────────────────
def log_event(lead_id, event_type, campaign_id=None, subject=None, message_id=None, note=None, org_id=None, account_id=None):
    with get_conn() as c:
        org = org_id or _lead_org(c, lead_id)
        c.execute(
            "INSERT INTO email_events "
            "(org_id, lead_id, campaign_id, event_type, subject, message_id, note, account_id, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (org_id_or_default(org), lead_id, campaign_id, event_type, subject,
             message_id, note, account_id, now()),
        )


def lead_has_event(lead_id, event_type, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            f"SELECT 1 FROM email_events WHERE lead_id=%s AND event_type=%s{clause} LIMIT 1",
            (lead_id, event_type, *args),
        ).fetchone()
        return row is not None


def events_for_lead(lead_id, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            f"SELECT * FROM email_events WHERE lead_id=%s{clause} ORDER BY created_at DESC",
            (lead_id, *args)
        ).fetchall()]


# ── Negotiation: threads, messages, drafts ───────────────────
def get_or_create_thread(lead_id, subject=None, org_id=None):
    with get_conn() as c:
        org = org_id or _lead_org(c, lead_id)
        row = c.execute(
            "SELECT id FROM vendor_threads WHERE lead_id=%s", (lead_id,)
        ).fetchone()
        if row:
            return row["id"]
        cur = c.execute(
            "INSERT INTO vendor_threads (org_id, lead_id, subject, status, created_at) "
            "VALUES (%s, %s, %s, 'active', %s) RETURNING id",
            (org_id_or_default(org), lead_id, subject, now()),
        )
        return cur.fetchone()["id"]


def update_thread(thread_id, org_id=None, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=%s" for k in fields)
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        c.execute(f"UPDATE vendor_threads SET {cols} WHERE id=%s{clause}",
                  (*fields.values(), thread_id, *args))


def set_thread_last_intent(thread_id, intent, org_id=None):
    update_thread(thread_id, org_id=org_id, last_intent=intent)


def get_thread(thread_id, org_id=None):
    clause, args = _scope(org_id, "vt.org_id")
    with get_conn() as c:
        row = c.execute(
            "SELECT vt.*, l.business_name, l.email, l.domain "
            "FROM vendor_threads vt JOIN leads l ON l.id = vt.lead_id "
            f"WHERE vt.id=%s{clause}", (thread_id, *args),
        ).fetchone()
        return dict(row) if row else None


def list_threads(intent=None, limit=100, org_id=None, inbound_only=False):
    where, params = "WHERE 1=1 ", []
    if intent:
        where += "AND vt.last_intent = %s "
        params.append(intent)
    if inbound_only:
        where += (
            "AND EXISTS (SELECT 1 FROM negotiation_messages inbound "
            "WHERE inbound.thread_id=vt.id AND inbound.direction='inbound') "
        )
    clause, org_args = _scope(org_id, "vt.org_id")
    where += clause
    params.extend(org_args)
    params.append(limit)
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT vt.*, l.business_name, l.email, l.domain, "
            "  l.country, l.city, l.status AS lead_status, "
            "  (SELECT COUNT(*) FROM negotiation_messages m "
            "   WHERE m.thread_id=vt.id AND m.direction='inbound') AS inbound_count, "
            "  (SELECT COUNT(*) FROM negotiation_messages m "
            "   WHERE m.thread_id=vt.id) AS total_msgs, "
            "  (SELECT COUNT(*) FROM negotiation_drafts d "
            "   WHERE d.thread_id=vt.id AND d.status='pending') AS pending_drafts, "
            "  (SELECT m.body FROM negotiation_messages m "
            "   WHERE m.thread_id=vt.id AND m.direction='inbound' "
            "   ORDER BY m.created_at DESC LIMIT 1) AS last_inbound_body, "
            "  (SELECT m.subject FROM negotiation_messages m "
            "   WHERE m.thread_id=vt.id AND m.direction='inbound' "
            "   ORDER BY m.created_at DESC LIMIT 1) AS last_inbound_subject, "
            "  (SELECT m.from_addr FROM negotiation_messages m "
            "   WHERE m.thread_id=vt.id AND m.direction='inbound' "
            "   ORDER BY m.created_at DESC LIMIT 1) AS last_inbound_from, "
            "  (SELECT m.created_at FROM negotiation_messages m "
            "   WHERE m.thread_id=vt.id AND m.direction='inbound' "
            "   ORDER BY m.created_at DESC LIMIT 1) AS last_inbound_at "
            f"FROM vendor_threads vt JOIN leads l ON l.id = vt.lead_id "
            f"{where} "
            "ORDER BY COALESCE(vt.last_inbound_at, vt.created_at) DESC LIMIT %s",
            params,
        ).fetchall()]


def insert_thread_message(thread_id, direction, subject, body,
                          message_id=None, in_reply_to=None,
                          from_addr=None, to_addr=None):
    with get_conn() as c:
        try:
            c.execute(
                "INSERT INTO negotiation_messages "
                "(thread_id, direction, subject, body, message_id, in_reply_to, "
                " from_addr, to_addr, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (thread_id, direction, subject, body, message_id, in_reply_to,
                 from_addr, to_addr, now()),
            )
        except pg_errors.UniqueViolation:
            return
        if direction == "inbound":
            c.execute(
                "UPDATE vendor_threads SET last_inbound_at=%s WHERE id=%s",
                (now(), thread_id),
            )


def thread_messages(thread_id):
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM negotiation_messages WHERE thread_id=%s ORDER BY created_at ASC",
            (thread_id,)
        ).fetchall()]


def insert_draft(thread_id, model, draft_subject, draft_body,
                 detected_price=None, detected_currency=None,
                 reasoning=None, suggested_action=None):
    with get_conn() as c:
        cur = c.execute(
            "INSERT INTO negotiation_drafts "
            "(thread_id, model, draft_subject, draft_body, detected_price, "
            " detected_currency, reasoning, suggested_action, status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s) RETURNING id",
            (thread_id, model, draft_subject, draft_body, detected_price,
             detected_currency, reasoning, suggested_action, now()),
        )
        return cur.fetchone()["id"]


def get_draft(draft_id, org_id=None):
    clause, args = _scope(org_id, "t.org_id")
    with get_conn() as c:
        row = c.execute(
            "SELECT d.* FROM negotiation_drafts d "
            "JOIN vendor_threads t ON t.id=d.thread_id "
            f"WHERE d.id=%s{clause}",
            (draft_id, *args),
        ).fetchone()
        return dict(row) if row else None


def pending_draft_for_thread(thread_id):
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM negotiation_drafts "
            "WHERE thread_id=%s AND status='pending' "
            "ORDER BY id DESC LIMIT 1",
            (thread_id,)
        ).fetchone()
        return dict(row) if row else None


def update_draft(draft_id, org_id=None, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=%s" for k in fields)
    clause, args = _scope(org_id, "t.org_id")
    with get_conn() as c:
        c.execute(
            "UPDATE negotiation_drafts SET " + cols +
            " WHERE id=%s AND thread_id IN "
            "(SELECT id FROM vendor_threads t WHERE 1=1" + clause + ")",
            (*fields.values(), draft_id, *args),
        )


# ── pending_replies: booking invites deferred until the lead's business hours

def enqueue_pending_reply(lead_id, thread_id, subject,
                          parent_message_id=None, parent_references=None,
                          due_at=None, org_id=None):
    with get_conn() as c:
        org = org_id or _thread_org(c, thread_id) or _lead_org(c, lead_id)
        cur = c.execute(
            "INSERT INTO pending_replies "
            "(org_id, lead_id, thread_id, subject, parent_message_id, parent_references, "
            " due_at, status, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s) RETURNING id",
            (org_id_or_default(org), lead_id, thread_id, subject, parent_message_id,
             parent_references, due_at or now(), now()),
        )
        return cur.fetchone()["id"]


def pending_reply_exists(lead_id, status="pending", org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            f"SELECT id FROM pending_replies WHERE lead_id=%s AND status=%s{clause} LIMIT 1",
            (lead_id, status, *args),
        ).fetchone()
        return row["id"] if row else None


def due_pending_replies(limit=10, org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM pending_replies WHERE status='pending' AND due_at <= %s"
            f"{clause} ORDER BY due_at ASC LIMIT %s",
            (now(), *args, limit),
        ).fetchall()]


def mark_pending_reply(row_id, status, error=None, org_id=None):
    clause, args = _scope(org_id, "org_id")
    if status == "sent":
        with get_conn() as c:
            c.execute(
                f"UPDATE pending_replies SET status='sent', sent_at=%s WHERE id=%s{clause}",
                (now(), row_id, *args),
            )
    else:
        with get_conn() as c:
            c.execute(
                f"UPDATE pending_replies SET status=%s, error=%s WHERE id=%s{clause}",
                (status, error, row_id, *args),
            )


def pending_replies_summary(org_id=None):
    clause, args = _scope(org_id, "org_id")
    with get_conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS total, "
            "  COUNT(*) FILTER (WHERE status='pending') AS pending, "
            "  COUNT(*) FILTER (WHERE status='failed') AS failed "
            f"FROM pending_replies WHERE 1=1{clause}",
            tuple(args),
        ).fetchone()
        return dict(row)


def create_deal(lead_id, thread_id, vendor_name, final_price, currency,
                terms=None, notes=None, org_id=None):
    with get_conn() as c:
        org = org_id or _lead_org(c, lead_id)
        cur = c.execute(
            "INSERT INTO deals (org_id, lead_id, thread_id, vendor_name, final_price, "
            "currency, terms, notes, closed_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING id",
            (org_id_or_default(org), lead_id, thread_id, vendor_name, final_price,
             currency, terms, notes, now()),
        )
        return cur.fetchone()["id"]


def list_deals(org_id=None):
    clause, args = _scope(org_id, "d.org_id")
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT d.*, l.business_name, l.email AS vendor_email "
            f"FROM deals d JOIN leads l ON l.id = d.lead_id WHERE 1=1{clause} "
            "ORDER BY d.closed_at DESC",
            tuple(args),
        ).fetchall()]
