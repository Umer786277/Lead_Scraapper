"use client";

import { api, type Lead, type LeadBucketCounts } from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

type Bucket = "all" | "call" | "email" | "pending" | "none";

const BUCKETS: { key: Bucket; label: string; icon: string; color: string }[] = [
  { key: "call",    label: "Cold Calling",       icon: "📞", color: "text-green-400  border-green-500/30  bg-green-500/5" },
  { key: "email",   label: "Email Outreach",      icon: "📧", color: "text-indigo-400 border-indigo-500/30 bg-indigo-500/5" },
  { key: "pending", label: "Pending Extraction",  icon: "🔍", color: "text-yellow-400 border-yellow-500/30 bg-yellow-500/5" },
  { key: "none",    label: "No Contact",          icon: "❌", color: "text-red-400    border-red-500/30    bg-red-500/5" },
];

const STATUS_COLORS: Record<string, string> = {
  new:        "bg-slate-700 text-slate-300",
  contacted:  "bg-blue-900/50 text-blue-300",
  replied:    "bg-green-900/50 text-green-300",
  converted:  "bg-purple-900/50 text-purple-300",
  dead:       "bg-red-900/30 text-red-400",
};

export default function LeadsPage() {
  const [bucket, setBucket] = useState<Bucket>("call");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [counts, setCounts] = useState<LeadBucketCounts | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [error, setError] = useState("");

  const fetchLeads = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const params: Record<string, string> = { bucket, limit: "300" };
      if (search) params.q = search;
      if (statusFilter !== "all") params.status = statusFilter;
      const res = await api.leads.list(params);
      setLeads(res.items);
      setCounts(res.bucket_counts);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [bucket, search, statusFilter]);

  useEffect(() => { fetchLeads(); }, [fetchLeads]);

  async function updateStatus(id: number, status: string) {
    await api.leads.updateStatus(id, status);
    setLeads((prev) => prev.map((l) => (l.id === id ? { ...l, status } : l)));
  }

  function downloadCSV() {
    if (!leads.length) return;
    const keys = Object.keys(leads[0]) as (keyof Lead)[];
    const rows = [keys.join(","), ...leads.map((l) => keys.map((k) => JSON.stringify(l[k] ?? "")).join(","))];
    const blob = new Blob([rows.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `leads_${bucket}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  const activeBucket = BUCKETS.find((b) => b.key === bucket)!;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">👥 Leads</h1>
        <p className="text-slate-400 text-sm mt-1">Segmented by contact readiness. Click a tab to filter.</p>
      </div>

      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-sm">{error}</div>
      )}

      {/* Bucket tabs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {BUCKETS.map(({ key, label, icon, color }) => {
          const count = counts ? counts[key] : "—";
          const active = bucket === key;
          return (
            <button
              key={key}
              onClick={() => setBucket(key)}
              className={`rounded-xl border px-4 py-3 text-left transition-all ${color} ${
                active ? "ring-2 ring-indigo-400/40" : "opacity-60 hover:opacity-100"
              }`}
            >
              <div className="text-xl mb-1">{icon}</div>
              <div className="font-bold text-white text-xl">{count}</div>
              <div className="text-xs mt-0.5 text-slate-400">{label}</div>
            </button>
          );
        })}
      </div>

      {/* Filters row */}
      <div className="flex flex-wrap gap-3 items-center">
        <input
          type="text"
          placeholder="Search name, city, domain…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-white/5 border border-white/10 rounded-lg px-4 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 w-64"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
        >
          {["all", "new", "contacted", "replied", "converted", "dead"].map((s) => (
            <option key={s} value={s} className="bg-[#1a1d2e]">{s === "all" ? "All statuses" : s}</option>
          ))}
        </select>
        <div className="flex-1" />
        <button
          onClick={downloadCSV}
          disabled={!leads.length}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-slate-300 transition disabled:opacity-40"
        >
          📥 Export CSV
        </button>
      </div>

      {/* Table */}
      <div className={`rounded-2xl border overflow-hidden ${activeBucket.color}`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10">
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Business</th>
                {bucket === "call"    && <th className="text-left px-4 py-3 text-slate-400 font-medium">Phone</th>}
                {bucket === "email"   && <th className="text-left px-4 py-3 text-slate-400 font-medium">Email</th>}
                {bucket === "pending" && <th className="text-left px-4 py-3 text-slate-400 font-medium">Domain</th>}
                <th className="text-left px-4 py-3 text-slate-400 font-medium">City</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Reviews</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Maps</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">AI Note</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Status</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {loading ? (
                [...Array(8)].map((_, i) => (
                  <tr key={i}>
                    {[...Array(6)].map((_, j) => (
                      <td key={j} className="px-4 py-3">
                        <div className="h-4 bg-white/5 rounded animate-pulse" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : leads.length === 0 ? (
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center text-slate-500">
                    No leads in this bucket yet.
                  </td>
                </tr>
              ) : (
                leads.map((lead) => (
                  <tr key={lead.id} className="hover:bg-white/3 transition-colors">
                    <td className="px-4 py-3 text-white font-medium max-w-[200px] truncate">
                      {lead.business_name || <span className="text-slate-500">—</span>}
                    </td>

                    {bucket === "call" && (
                      <td className="px-4 py-3 text-green-400 font-mono text-xs">{lead.phone}</td>
                    )}
                    {bucket === "email" && (
                      <td className="px-4 py-3 text-indigo-300 text-xs">{lead.email}</td>
                    )}
                    {bucket === "pending" && (
                      <td className="px-4 py-3 text-slate-300 text-xs">{lead.domain}</td>
                    )}

                    <td className="px-4 py-3 text-slate-400">{lead.city || "—"}</td>

                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {lead.reviews != null ? (
                        <span className="flex items-center gap-1">
                          ⭐ {lead.rating?.toFixed(1) ?? "?"} <span className="text-slate-500">({lead.reviews})</span>
                        </span>
                      ) : "—"}
                    </td>

                    <td className="px-4 py-3">
                      {lead.maps_url ? (
                        <a href={lead.maps_url} target="_blank" rel="noopener noreferrer"
                          className="text-xs text-indigo-400 hover:text-indigo-300 underline">
                          View
                        </a>
                      ) : <span className="text-slate-600">—</span>}
                    </td>

                    <td className="px-4 py-3 max-w-[220px]">
                      {lead.improvement_note ? (
                        <span className="text-xs text-yellow-300/80 leading-relaxed" title={lead.improvement_note}>
                          {lead.improvement_note.slice(0, 80)}{lead.improvement_note.length > 80 ? "…" : ""}
                        </span>
                      ) : <span className="text-slate-600 text-xs">—</span>}
                    </td>

                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[lead.status] ?? "bg-slate-700 text-slate-300"}`}>
                        {lead.status}
                      </span>
                    </td>

                    <td className="px-4 py-3">
                      <select
                        value={lead.status}
                        onChange={(e) => updateStatus(lead.id, e.target.value)}
                        className="bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-slate-300 focus:outline-none"
                      >
                        {["new", "contacted", "replied", "converted", "dead"].map((s) => (
                          <option key={s} value={s} className="bg-[#1a1d2e]">{s}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {!loading && leads.length > 0 && (
          <div className="px-4 py-3 border-t border-white/8 text-xs text-slate-500">
            Showing {leads.length} lead{leads.length !== 1 ? "s" : ""}
          </div>
        )}
      </div>
    </div>
  );
}
