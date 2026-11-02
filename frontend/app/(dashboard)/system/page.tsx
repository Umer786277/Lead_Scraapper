"use client";

import { api } from "@/lib/api";
import { Settings } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

export default function SystemPage() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [data, setData]         = useState<any>(null);
  const [error, setError]       = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);

  const fetchStatus = useCallback(async () => {
    try {
      const d = await api.system.status();
      setData(d);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    // Load external API state after mount; updates happen after the request resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    fetchStatus();
  }, [fetchStatus]);

  useEffect(() => {
    if (autoRefresh) {
      const timer = setInterval(fetchStatus, 4000);
      return () => clearInterval(timer);
    }
  }, [autoRefresh, fetchStatus]);

  const worker = data?.worker ?? {};
  const queue  = data?.queue  ?? {};
  const config = data?.config ?? {};
  const workerRunning = !!worker.scheduler_running;

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-white">
            <Settings className="text-indigo-400" size={24} /> System
          </h1>
          <p className="text-slate-400 text-sm mt-1">Worker health and organization-specific queue status.</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-slate-400 cursor-pointer select-none">
            <span className={`w-2 h-2 rounded-full ${autoRefresh ? "bg-green-400 animate-pulse" : "bg-slate-600"}`} />
            <input
              type="checkbox"
              className="hidden"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            {autoRefresh ? "Live" : "Paused"}
          </label>
          <button
            onClick={fetchStatus}
            className="px-4 py-2 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 text-slate-300 text-sm transition"
          >
            Refresh
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-sm">
          {error.includes("fetch") ? "Cannot reach the API — is FastAPI running?" : error}
        </div>
      )}

      {/* Worker status */}
      <div className={`rounded-2xl border px-6 py-4 ${
        workerRunning       ? "border-green-500/20 bg-green-500/5"   :
                              "border-yellow-500/20 bg-yellow-500/5"
      }`}>
        <div className="flex items-center gap-3">
          <div className={`w-3 h-3 rounded-full ${workerRunning ? "bg-green-400 animate-pulse" : "bg-slate-500"}`} />
          <div>
            <p className="font-semibold text-white">
              {workerRunning ? "Background worker running" : "Background worker status unavailable"}
            </p>
            <p className="text-xs text-slate-400 mt-0.5">
              Scheduled tasks run across organizations; tenant-scoped manual triggers and shared logs are disabled.
            </p>
          </div>
        </div>
      </div>

      {/* Queue depth */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Send Queue</h2>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {(["pending", "sending", "sent", "skipped", "failed"] as const).map((s) => (
            <div key={s} className="rounded-xl border border-white/8 bg-white/3 px-4 py-4">
              <p className="text-xs text-slate-400 capitalize mb-1">{s}</p>
              <p className={`text-2xl font-bold ${s === "failed" && (queue[s] ?? 0) > 0 ? "text-red-400" : "text-white"}`}>
                {queue[s] ?? 0}
              </p>
            </div>
          ))}
        </div>
      </div>

      {/* Config checklist */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Configuration</h2>
        <div className="rounded-2xl border border-white/8 overflow-hidden divide-y divide-white/5">
          {[
            { key: "database", label: "DATABASE_URL",         desc: "PostgreSQL / Supabase" },
            { key: "smtp",     label: "SMTP_HOST",            desc: "Email sending" },
            { key: "imap",     label: "IMAP_HOST",            desc: "Reply detection (optional)" },
            { key: "gemini",  label: "GOOGLE_AI_STUDIO_API_KEY", desc: "AI features (optional)" },
            { key: "supabase", label: "Supabase auth",         desc: "SUPABASE_URL or legacy JWT secret" },
            { key: "vapi",     label: "VAPI_API_KEY",         desc: "Voice calling (+ VAPI_PHONE_NUMBER_ID)" },
            { key: "booking_link", label: "BOOKING_LINK",     desc: "Auto-reply scheduling link (optional)" },
          ].map(({ key, label, desc }) => {
            const ok = config[key];
            return (
              <div key={key} className="flex items-center justify-between px-5 py-3">
                <div>
                  <span className="text-sm text-white font-mono">{label}</span>
                  <span className="text-xs text-slate-500 ml-2">{desc}</span>
                </div>
                <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${
                  ok ? "bg-green-900/50 text-green-400" : "bg-red-900/30 text-red-400"
                }`}>
                  {ok ? "configured" : "missing"}
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
