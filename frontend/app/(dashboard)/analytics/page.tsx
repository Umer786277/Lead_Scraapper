"use client";

import { api } from "@/lib/api";
import { useEffect, useState } from "react";

export default function AnalyticsPage() {
  const [overview, setOverview] = useState<Record<string, number> | null>(null);
  const [domains, setDomains] = useState<{ domain: string; email_count: number; high_conf: number }[]>([]);
  const [statuses, setStatuses] = useState<{ status: string; n: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api.analytics.overview(),
      api.analytics.topDomains(20),
      api.analytics.leadStatuses(),
    ])
      .then(([ov, dom, st]) => {
        setOverview(ov as unknown as Record<string, number>);
        setDomains(dom as typeof domains);
        setStatuses(st as typeof statuses);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  const METRICS = overview ? [
    { label: "Total leads",         val: overview.leads_total,      color: "text-white" },
    { label: "Emails extracted",    val: overview.emails_total,     color: "text-indigo-400" },
    { label: "High confidence",     val: overview.emails_high_conf, color: "text-green-400" },
    { label: "Ready to call",       val: overview.leads_callable,   color: "text-green-400" },
    { label: "Ready to email",      val: overview.leads_emailable,  color: "text-indigo-400" },
    { label: "Pending extraction",  val: overview.leads_pending,    color: "text-yellow-400" },
  ] : [];

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-white">📊 Analytics</h1>
        <p className="text-slate-400 text-sm mt-1">Unified numbers across all runs and lead sources.</p>
      </div>

      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-sm">
          {error.includes("fetch") ? "Cannot reach the API — is FastAPI running?" : error}
        </div>
      )}

      {/* KPI grid */}
      {loading ? (
        <div className="grid grid-cols-3 gap-4">
          {[...Array(6)].map((_, i) => <div key={i} className="h-24 rounded-2xl bg-white/5 animate-pulse" />)}
        </div>
      ) : (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
          {METRICS.map(({ label, val, color }) => (
            <div key={label} className="rounded-2xl border border-white/8 bg-white/3 px-5 py-4">
              <p className="text-xs text-slate-400 mb-1">{label}</p>
              <p className={`text-3xl font-bold ${color}`}>{(val ?? 0).toLocaleString()}</p>
            </div>
          ))}
        </div>
      )}

      {/* Lead statuses */}
      {statuses.length > 0 && (
        <div className="rounded-2xl border border-white/8 bg-white/3 px-6 py-5">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-4">Leads by status</h2>
          <div className="space-y-3">
            {statuses.map(({ status, n }) => {
              const total = statuses.reduce((s, r) => s + r.n, 0);
              const pct = total ? Math.round((n / total) * 100) : 0;
              return (
                <div key={status}>
                  <div className="flex justify-between text-sm mb-1">
                    <span className="text-slate-300 capitalize">{status}</span>
                    <span className="text-slate-400">{n.toLocaleString()} <span className="text-slate-500">({pct}%)</span></span>
                  </div>
                  <div className="h-2 bg-white/5 rounded-full overflow-hidden">
                    <div className="h-full bg-indigo-500 rounded-full transition-all" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Top domains */}
      {domains.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Top domains by email count</h2>
          <div className="rounded-2xl border border-white/8 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/8 bg-white/3">
                  <th className="text-left px-5 py-3 text-slate-400 font-medium">Domain</th>
                  <th className="text-right px-5 py-3 text-slate-400 font-medium">Emails</th>
                  <th className="text-right px-5 py-3 text-slate-400 font-medium">High conf.</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {domains.map((d) => (
                  <tr key={d.domain} className="hover:bg-white/3 transition-colors">
                    <td className="px-5 py-3 text-white">{d.domain}</td>
                    <td className="px-5 py-3 text-right text-slate-300">{d.email_count}</td>
                    <td className="px-5 py-3 text-right text-green-400">{d.high_conf}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
