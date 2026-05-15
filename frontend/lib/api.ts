import { createClient } from "./supabase";

const API_BASE = ""; // proxied via next.config.ts rewrites → no CORS

async function authHeaders(): Promise<Record<string, string>> {
  const supabase = createClient();
  const {
    data: { session },
  } = await supabase.auth.getSession();
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${session?.access_token ?? ""}`,
  };
}

async function apiFetch<T = unknown>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const headers = await authHeaders();
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { ...headers, ...(options.headers as Record<string, string>) },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

// ── Typed API surface ────────────────────────────────────────
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
  last_review_days: number | null;
  status: string;
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

export const api = {
  leads: {
    list: (params: Record<string, string> = {}) => {
      const qs = new URLSearchParams(params).toString();
      return apiFetch<LeadsResponse>(`/api/leads${qs ? "?" + qs : ""}`);
    },
    get: (id: number) => apiFetch<Lead>(`/api/leads/${id}`),
    updateStatus: (id: number, status: string, notes?: string) =>
      apiFetch(`/api/leads/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ status, notes }),
      }),
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
    templates: () => apiFetch("/api/outreach/templates"),
    campaigns: () => apiFetch("/api/outreach/campaigns"),
    queue: () => apiFetch("/api/outreach/queue"),
    send: (dryRun = false) =>
      apiFetch(`/api/outreach/send?dry_run=${dryRun}`, { method: "POST" }),
  },

  system: {
    status: () => apiFetch("/api/system/status"),
    logs: (lastN = 150) => apiFetch<{ lines: string[] }>(`/api/system/logs?last_n=${lastN}`),
    triggerJob: (jobId: string) => apiFetch<{ ok: boolean; job_id: string }>(`/api/system/jobs/${jobId}/trigger`, { method: "POST" }),
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
};
