"use client";

import StatCard from "@/components/StatCard";
import { api, type OverviewStats } from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

export default function DashboardPage() {
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [trend, setTrend] = useState<{ day: string; n: number }[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [error, setError] = useState("");

  const fetchStats = useCallback(async (silent = false) => {
    if (!silent) setRefreshing(true);
    setError("");
    try {
      const [s, t] = await Promise.all([
        api.analytics.overview(),
        api.analytics.trend(14),
      ]);
      setStats(s);
      setTrend(t);
      setLastUpdated(new Date());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchStats();
    const interval = setInterval(() => fetchStats(true), 30_000);
    return () => clearInterval(interval);
  }, [fetchStats]);

  return (
    <div className="space-y-8">
      {/* Hero */}
      <div className="rounded-2xl bg-gradient-to-br from-indigo-600/20 to-purple-600/10 border border-indigo-500/20 px-8 py-6 flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white mb-1">🎯 LeadFlow Dashboard</h1>
          <p className="text-slate-400 text-sm">
            Scrape · Enrich · Outreach — all in one place.
          </p>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <button
            onClick={() => fetchStats()}
            disabled={refreshing}
            className="flex items-center gap-2 px-3 py-1.5 text-xs bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-slate-300 transition disabled:opacity-50"
          >
            <span className={refreshing ? "animate-spin" : ""}>↻</span>
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
          {lastUpdated && (
            <span className="text-xs text-slate-600">
              Updated {lastUpdated.toLocaleTimeString()}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-sm">
          {error}
        </div>
      )}

      {/* Pipeline status row */}
      {loading ? (
        <div className="grid grid-cols-4 gap-4">
          {[...Array(4)].map((_, i) => (
            <div key={i} className="h-28 rounded-2xl bg-white/5 animate-pulse" />
          ))}
        </div>
      ) : stats ? (
        <>
          {/* Headline metrics */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard label="Total leads"        value={stats.leads_total.toLocaleString()} />
            <StatCard label="Emails extracted"   value={stats.emails_total.toLocaleString()} color="indigo" />
            <StatCard label="Domains scanned"    value={stats.domains_total.toLocaleString()} />
            <StatCard label="High confidence"    value={stats.emails_high_conf.toLocaleString()} color="green"
              sub={stats.emails_total ? `${Math.round(stats.emails_high_conf / stats.emails_total * 100)}% of emails` : undefined}
            />
          </div>

          {/* Pipeline buckets */}
          <div>
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Lead pipeline</h2>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              <StatCard label="📞 Ready to call"       value={stats.leads_callable.toLocaleString()}   color="green" />
              <StatCard label="📧 Ready to email"      value={stats.leads_emailable.toLocaleString()}  color="indigo" />
              <StatCard label="🔍 Pending extraction"  value={stats.leads_pending.toLocaleString()}    color="yellow" sub="worker will enrich" />
              <StatCard label="❌ No contact info"     value={stats.leads_no_contact.toLocaleString()} color="red" />
            </div>
          </div>

          {/* Extraction trend mini chart */}
          {trend.length > 0 && (
            <div>
              <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
                Emails extracted — last 14 days
              </h2>
              <div className="rounded-2xl border border-white/8 bg-white/3 px-6 py-5">
                <div className="flex items-end gap-1 h-24">
                  {(() => {
                    const max = Math.max(...trend.map((d) => d.n), 1);
                    return trend.map((d) => (
                      <div key={d.day} className="flex-1 flex flex-col items-center gap-1 group">
                        <div
                          className="w-full rounded-t bg-indigo-500/60 hover:bg-indigo-400 transition-all"
                          style={{ height: `${(d.n / max) * 100}%`, minHeight: d.n ? 4 : 0 }}
                          title={`${d.day}: ${d.n}`}
                        />
                      </div>
                    ));
                  })()}
                </div>
                <div className="flex justify-between text-xs text-slate-500 mt-2">
                  <span>{trend[0]?.day}</span>
                  <span>{trend[trend.length - 1]?.day}</span>
                </div>
              </div>
            </div>
          )}
        </>
      ) : null}

      {/* Quick links */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Quick actions</h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[
            { href: "/scrape",   icon: "🚀", title: "Scrape & Enrich",  desc: "Google Maps → leads → emails in one click" },
            { href: "/leads",    icon: "👥", title: "View Leads",        desc: "Browse, filter, and export your lead pipeline" },
            { href: "/outreach", icon: "✉️",  title: "Outreach",         desc: "Campaigns, templates, send queue" },
          ].map(({ href, icon, title, desc }) => (
            <a
              key={href}
              href={href}
              className="group rounded-2xl border border-white/8 bg-white/3 hover:bg-white/6 hover:border-indigo-500/30 px-6 py-5 transition-all"
            >
              <div className="text-2xl mb-2">{icon}</div>
              <div className="font-semibold text-white mb-1">{title}</div>
              <div className="text-sm text-slate-400">{desc}</div>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
