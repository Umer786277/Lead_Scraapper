import { createClient } from "./supabase";

const API_BASE = ""; // proxied via next.config.ts rewrites â†’ no CORS

const ORG_STORAGE_KEY = "active_org_id";

export function getActiveOrgId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ORG_STORAGE_KEY);
}

export function setActiveOrgId(orgId: string | null): void {
  if (typeof window === "undefined") return;
  if (orgId) window.localStorage.setItem(ORG_STORAGE_KEY, orgId);
  else window.localStorage.removeItem(ORG_STORAGE_KEY);
}

async function authHeaders(includeJsonContentType = true): Promise<Record<string, string>> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  const orgId = getActiveOrgId();
  return {
    ...(includeJsonContentType ? { "Content-Type": "application/json" } : {}),
    Authorization: `Bearer ${session?.access_token ?? ""}`,
    ...(orgId ? { "X-Org-Id": orgId } : {}),
  };
}

async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
  const headers = await authHeaders(!isFormData);
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    let message = text || res.statusText || "The request failed.";
    try {
      const body: unknown = JSON.parse(text);
      if (body && typeof body === "object" && "detail" in body) {
        const detail = body.detail;
        if (typeof detail === "string") {
          message = detail;
        } else if (Array.isArray(detail)) {
          message = detail
            .map((item) =>
              item && typeof item === "object" && "msg" in item && typeof item.msg === "string"
                ? item.msg
                : ""
            )
            .filter(Boolean)
            .join("; ") || message;
        }
      }
    } catch {
      // Keep the response text when the server does not return JSON.
    }
    throw new Error(message);
  }
  if (!res.headers.get("content-type")?.includes("application/json")) {
    throw new Error("The API returned a non-JSON response. Check the frontend API rewrite and backend status.");
  }
  return res.json() as Promise<T>;
}

// â”€â”€ Typed API surface â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
export interface Organization {
  id: string;
  name: string;
  slug: string;
  status: string;
  role: "super_admin" | "admin" | "user";
  deletion_protected?: boolean;
}

export interface OrganizationMember {
  user_id: string;
  email: string | null;
  role: "admin" | "user";
  created_at: string;
  access_status: "member" | "pending";
}

export interface PlatformDirectoryUser {
  id: string;
  email: string | null;
  created_at: string;
  last_sign_in_at: string | null;
  email_verified: boolean;
  platform_role: "super_admin" | null;
  organizations: {
    org_id: string;
    org_name: string;
    role: "admin" | "user";
    access_status: "member" | "pending";
  }[];
}

export interface DeletionJob {
  id: string;
  kind: "organization" | "user";
  target_id: string;
  target_label: string;
  status: "queued" | "processing" | "completed" | "failed";
  attempts: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface EmailAccount {
  id: number;
  label: string | null;
  from_name: string | null;
  from_email: string;
  reply_to: string | null;
  smtp_host: string;
  smtp_port: number;
  smtp_user: string | null;
  smtp_use_tls: boolean;
  imap_host: string | null;
  imap_port: number;
  imap_user: string | null;
  imap_mailbox: string | null;
  daily_limit: number;
  hourly_limit: number;
  warmup_started_at: string | null;
  warmup_days: number;
  status: string;
  last_used_at: string | null;
  has_smtp_password: boolean;
  has_imap_password: boolean;
}

export interface LeadBucketCounts {
  all: number;
  call: number;
  email: number;
  pending: number;
  none: number;
}

export interface Lead {
  id: number;
  source: string;
  business_name: string | null;
  domain: string | null;
  email: string | null;
  phone: string | null;
  website: string | null;
  city: string | null;
  country: string | null;
  rating: number | null;
  reviews: number | null;
  maps_url: string | null;
  improvement_note: string | null;
  lead_line: string | null;
  last_review_days: number | null;
  status: string;
  do_not_contact?: boolean;
  created_at: string;
}

export interface LeadsResponse {
  items: Lead[];
  total: number;
  bucket_counts: LeadBucketCounts;
}

export interface OverviewStats {
  leads_total: number;
  emails_total: number;
  domains_total: number;
  emails_high_conf: number;
  leads_callable: number;
  leads_emailable: number;
  leads_pending: number;
  leads_no_contact: number;
}

export interface ThreadItem {
  id: number;
  lead_id: number;
  subject: string | null;
  status: string;
  last_intent: string | null;
  price_floor: number | null;
  currency: string | null;
  goal: string | null;
  business_name: string | null;
  email: string | null;
  city: string | null;
  country: string | null;
  lead_status: string;
  pending_drafts: number;
  last_inbound_subject: string | null;
  last_inbound_body: string | null;
  last_inbound_from: string | null;
  last_inbound_at: string | null;
}

export interface ThreadMessage {
  id: number;
  thread_id: number;
  direction: "inbound" | "outbound";
  subject: string | null;
  body: string | null;
  from_addr: string | null;
  to_addr: string | null;
  created_at: string;
}

export interface DraftResponse {
  draft_subject: string;
  draft_body: string;
  detected_price: number | null;
  detected_currency: string | null;
  reasoning: string;
  suggested_action: string | null;
  model: string;
}

export const api = {
  leads: {
    list: (params: Record<string, string> = {}) => {
      const qs = new URLSearchParams(params).toString();
      return apiFetch<LeadsResponse>(`/api/leads${qs ? "?" + qs : ""}`);
    },
    get: (id: number) => apiFetch<Lead>(`/api/leads/${id}`),
    importCsv: (file: File) => {
      const body = new FormData();
      body.append("file", file);
      return apiFetch<{
        imported: number;
        skipped: number;
        invalid: number;
        errors: string[];
      }>("/api/leads/import-csv", { method: "POST", body });
    },
    updateStatus: (id: number, status: string, notes?: string) =>
      apiFetch(`/api/leads/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, notes }),
      }),
    exclude: (leadIds: number[]) =>
      apiFetch<{ ok: boolean; updated: number }>("/api/leads/exclude", {
        method: "POST",
        body: JSON.stringify({ lead_ids: leadIds }),
      }),
    unexclude: (leadIds: number[]) =>
      apiFetch<{ ok: boolean; updated: number }>("/api/leads/unexclude", {
        method: "POST",
        body: JSON.stringify({ lead_ids: leadIds }),
      }),
    countries: () =>
      apiFetch<{ countries: string[] }>("/api/leads/meta/countries"),
    cities: (country?: string) =>
      apiFetch<{ cities: string[] }>(
        `/api/leads/meta/cities${country ? `?country=${encodeURIComponent(country)}` : ""}`
      ),
  },

  analytics: {
    overview: () => apiFetch<OverviewStats>("/api/analytics/overview"),
    trend: (days = 14) =>
      apiFetch<{ day: string; n: number }[]>(`/api/analytics/trend?days=${days}`),
    topDomains: (limit = 25) =>
      apiFetch<{ domain: string; email_count: number; high_conf: number }[]>(
        `/api/analytics/top-domains?limit=${limit}`
      ),
    leadStatuses: () =>
      apiFetch<{ status: string; n: number }[]>("/api/analytics/lead-statuses"),
  },

  pipeline: {
    run: (body: {
      searches: { niche: string; city: string; country: string }[];
      max_leads?: number;
      headless?: boolean;
      enrich_emails?: boolean;
    }) =>
      apiFetch<{ run_id: number; status: string }>("/api/pipeline/run", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    runs: () => apiFetch("/api/pipeline/runs"),
    schedules: {
      list: () =>
        apiFetch<
          {
            id: number;
            niche: string;
            country: string;
            target_leads: number;
            status: string;
            created_at: string;
          }[]
        >("/api/pipeline/schedules"),
      create: (body: { niche: string; country: string; target_leads?: number }) =>
        apiFetch<{ schedule_id: number; queries_seeded: number }>(
          "/api/pipeline/schedules",
          { method: "POST", body: JSON.stringify(body) }
        ),
      cancel: (id: number) =>
        apiFetch<{ ok: boolean }>(`/api/pipeline/schedules/${id}`, {
          method: "DELETE",
        }),
    },
  },

  outreach: {
    accounts: () =>
      apiFetch<{ items: EmailAccount[] }>("/api/outreach/accounts"),
    createAccount: (body: Partial<EmailAccount> & {
      from_email: string;
      smtp_host: string;
      smtp_password?: string;
      imap_password?: string;
    }) =>
      apiFetch<{ id: number }>("/api/outreach/accounts", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    updateAccount: (id: number, body: Partial<EmailAccount> & {
      smtp_password?: string;
      imap_password?: string;
    }) =>
      apiFetch<{ ok: boolean }>(`/api/outreach/accounts/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    deleteAccount: (id: number) =>
      apiFetch<{ ok: boolean }>(`/api/outreach/accounts/${id}`, {
        method: "DELETE",
      }),
    testAccount: (id: number) =>
      apiFetch<{ smtp_ok: boolean; imap_ok: boolean; errors: string[] }>(
        `/api/outreach/accounts/${id}/test`,
        { method: "POST" }
      ),
    templates: () => apiFetch("/api/outreach/templates"),
    campaigns: () => apiFetch("/api/outreach/campaigns"),
    queue: () => apiFetch("/api/outreach/queue"),
    limits: () =>
      apiFetch<{
        max_per_day: number;
        max_per_hour: number;
        max_emails_per_minute: number;
        hourly_allocation_limit: number;
        send_max_per_domain_per_hour: number;
        send_jitter_max_seconds: number;
        effective_hourly_allocation_limit: number;
        sent_today: number;
        sent_this_hour: number;
        paused: boolean;
        paused_until: string | null;
        pause_reason: string | null;
        pause_active: boolean;
      }>("/api/outreach/limits"),
    updateLimits: (body: { max_emails_per_minute: number }) =>
      apiFetch<{ max_emails_per_minute: number }>("/api/outreach/limits", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    updateHourlyAllocationLimit: (body: { hourly_allocation_limit: number }) =>
      apiFetch<{ hourly_allocation_limit: number }>("/api/outreach/hourly-allocation-limit", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    updateDomainHourlyLimit: (body: { send_max_per_domain_per_hour: number }) =>
      apiFetch<{ send_max_per_domain_per_hour: number }>("/api/outreach/domain-hourly-limit", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    updateSendJitter: (body: { send_jitter_max_seconds: number }) =>
      apiFetch<{ send_jitter_max_seconds: number }>("/api/outreach/send-jitter", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    send: (dryRun = false, limit = 20) =>
      apiFetch(`/api/outreach/send?dry_run=${dryRun}&limit=${limit}`, { method: "POST" }),
    sendNow: (body: { lead_ids: number[]; template_id?: number; dry_run?: boolean }) =>
      apiFetch("/api/outreach/send-now", { method: "POST", body: JSON.stringify(body) }),
    createCampaign: (body: {
      name: string;
      lead_ids: number[];
      steps: { template_id: number; delay_days: number }[];
      notes?: string;
    }) =>
      apiFetch<{ id: number }>("/api/outreach/campaigns", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    setCampaignStatus: (id: number, status: string) =>
      apiFetch(`/api/outreach/campaigns/${id}/status?status=${status}`, { method: "PATCH" }),
    sends: (status?: string, limit = 50, offset = 0) =>
      apiFetch<{ items: Record<string, unknown>[]; total: number }>(
        `/api/outreach/sends?limit=${limit}&offset=${offset}${status ? `&status=${status}` : ""}`
      ),
    threads: {
      inbox: (limit = 100) =>
        apiFetch<ThreadItem[]>(`/api/outreach/threads/inbox?limit=${limit}`),
      list: (intent?: string, limit = 100) =>
        apiFetch<ThreadItem[]>(
          `/api/outreach/threads${intent ? `?intent=${encodeURIComponent(intent)}` : ""}&limit=${limit}`
        ),
      get: (id: number) =>
        apiFetch<{
          thread: Record<string, unknown>;
          messages: ThreadMessage[];
          pending_draft: Record<string, unknown> | null;
        }>(`/api/outreach/threads/${id}`),
      generateDraft: (id: number) =>
        apiFetch<{ draft_id: number; draft: DraftResponse }>(`/api/outreach/threads/${id}/draft`, {
          method: "POST",
          body: JSON.stringify({}),
        }),
      regenerateDraft: (id: number, draftId: number) =>
        apiFetch<{ draft_id: number; draft: DraftResponse }>(
          `/api/outreach/threads/${id}/draft/${draftId}/regenerate`,
          { method: "POST", body: JSON.stringify({}) }
        ),
      sendDraft: (
        id: number,
        draftId: number,
        body: { subject: string; body: string; draft_id: number }
      ) =>
        apiFetch<{ ok: boolean; queued: boolean; outbound_id: number; message_id: string | null }>(
          `/api/outreach/threads/${id}/draft/${draftId}/send`,
          { method: "POST", body: JSON.stringify(body) }
        ),
    },
  },

  system: {
    status: () => apiFetch("/api/system/status"),
    logs: (lastN = 150) => apiFetch<{ lines: string[] }>(`/api/system/logs?last_n=${lastN}`),
    triggerJob: (jobId: string) => apiFetch<{ ok: boolean; job_id: string }>(`/api/system/jobs/${jobId}/trigger`, { method: "POST" }),
    pause: (paused: boolean) =>
      apiFetch<{ paused: boolean }>("/api/system/pause", {
        method: "POST",
        body: JSON.stringify({ paused }),
      }),
  },

  calls: {
    list: (limit = 100, offset = 0) =>
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      apiFetch<{ items: any[] }>(`/api/calls?limit=${limit}&offset=${offset}`),
    summary: () => apiFetch<Record<string, number>>("/api/calls/summary"),
    queue: (leadIds: number[]) =>
      apiFetch<{ queued: number; skipped: number; call_ids: number[] }>("/api/calls/queue", {
        method: "POST",
        body: JSON.stringify({ lead_ids: leadIds }),
      }),
  },

  orgs: {
    list: () => apiFetch<{ items: Organization[]; is_super_admin: boolean }>("/api/orgs"),
    current: () => apiFetch<{ org_id: string }>("/api/orgs/current"),
    create: (name: string, slug?: string) =>
      apiFetch<{ id: string }>("/api/orgs", {
        method: "POST",
        body: JSON.stringify({ name, slug }),
      }),
    directory: (limit = 100, offset = 0) =>
      apiFetch<{
        organizations: Organization[];
        users: { items: PlatformDirectoryUser[]; total: number };
        current_user_id: string;
      }>(`/api/orgs/directory?limit=${limit}&offset=${offset}`),
    deletionJobs: () =>
      apiFetch<{ items: DeletionJob[] }>("/api/orgs/deletion-jobs"),
    deleteOrganization: (orgId: string) =>
      apiFetch<{
        ok: boolean;
        job_id: string;
        status: "deleted" | "queued" | "already_queued" | "processing" | "failed";
      }>(`/api/orgs/${encodeURIComponent(orgId)}`, {
        method: "DELETE",
      }),
    deleteUser: (userId: string) =>
      apiFetch<{
        ok: boolean;
        job_id: string;
        status: "deleted" | "queued" | "already_queued" | "processing" | "failed";
      }>(
        `/api/orgs/users/${encodeURIComponent(userId)}`,
        { method: "DELETE" }
      ),
    lookupInvitation: (token: string) =>
      apiFetch<{ email: string; organization_name: string; account_exists: boolean }>(
        "/api/orgs/invitations/lookup",
        { method: "POST", body: JSON.stringify({ token }) }
      ),
    createInvitedAccount: (token: string, password: string) =>
      apiFetch<{ ok: boolean; email: string }>(
        "/api/orgs/invitations/signup",
        { method: "POST", body: JSON.stringify({ token, password }) }
      ),
    acceptInvitations: (token?: string) =>
      apiFetch<{ ok: boolean; items: { org_id: string; role: "admin" | "user" }[] }>(
        "/api/orgs/invitations/accept",
        { method: "POST", body: JSON.stringify(token ? { token } : {}) }
      ),
    members: {
      list: () =>
        apiFetch<{
          items: OrganizationMember[];
          can_manage: boolean;
          can_manage_roles: boolean;
          is_super_admin: boolean;
        }>("/api/orgs/members"),
      invite: (email: string, role: OrganizationMember["role"]) =>
        apiFetch<{
          ok: boolean;
          email: string;
          user_id: string | null;
          role: OrganizationMember["role"];
          invitation_required: boolean;
        }>(
          "/api/orgs/members/invite",
          { method: "POST", body: JSON.stringify({ email, role }) }
        ),
      updateRole: (userId: string, role: OrganizationMember["role"]) =>
        apiFetch<{ ok: boolean }>(
          `/api/orgs/members/${encodeURIComponent(userId)}`,
          { method: "PATCH", body: JSON.stringify({ role }) }
        ),
      remove: (userId: string) =>
        apiFetch<{ ok: boolean }>(
          `/api/orgs/members/${encodeURIComponent(userId)}`,
          { method: "DELETE" }
        ),
      revokeInvitation: (userId: string) =>
        apiFetch<{ ok: boolean }>(
          `/api/orgs/invitations/${encodeURIComponent(userId)}`,
          { method: "DELETE" }
        ),
    },
  },
};
