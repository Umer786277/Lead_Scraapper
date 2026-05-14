"use client";

import { api } from "@/lib/api";
import { useCallback, useEffect, useRef, useState } from "react";

const JOBS = [
  { id: "enrich",   icon: "🔍", label: "Enrich",   interval: "5 min",  fields: ["found", "enriched", "enrolled"] },
  { id: "send",     icon: "📤", label: "Send",      interval: "2 min",  fields: ["sent", "failed", "skipped"] },
  { id: "inbox",    icon: "📥", label: "Inbox",     interval: "15 min", fields: ["replies"] },
  { id: "rotation", icon: "🔄", label: "Rotation",  interval: "1 hr",   fields: ["leads_added", "query"] },
];

export default function SystemPage() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [data, setData]         = useState<any>(null);
  const [logs, setLogs]         = useState<string[]>([]);
  const [error, setError]       = useState("");
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [triggering, setTriggering]   = useState<string | null>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const d = await api.system.status();
      setData(d);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const fetchLogs = useCallback(async () => {
    try {
      const res = await api.system.logs(150);
      setLogs(res.lines ?? []);
      // Auto-scroll to bottom
      setTimeout(() => {
        if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
      }, 50);
    } catch {
      // silently ignore — logs are best-effort
    }
  }, []);

  async function triggerJob(jobId: string) {
    setTriggering(jobId);
    try {
      await api.system.triggerJob(jobId);
      setTimeout(() => { fetchStatus(); fetchLogs(); }, 1500);
    } finally {
      setTimeout(() => setTriggering(null), 1500);
    }
  }

  // Initial load
  useEffect(() => {
    fetchStatus();
    fetchLogs();
  }, [fetchStatus, fetchLogs]);

  // Auto-refresh every 4 seconds
  useEffect(() => {
    if (autoRefresh) {
      timerRef.current = setInterval(() => {
        fetchStatus();
        fetchLogs();
      }, 4000);
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [autoRefresh, fetchStatus, fetchLogs]);

  const worker = data?.worker ?? {};
  const queue  = data?.queue  ?? {};
  const config = data?.config ?? {};
  const jobs   = worker?.jobs ?? {};
  const workerRunning = worker.scheduler_running ?? (!worker.stopped_at && !!worker.started_at);

  function logColor(line: string) {
    if (line.includes("[ERROR]"))   return "text-red-400";
    if (line.includes("[WARNING]")) return "text-yellow-400";
    if (line.includes("crashed"))   return "text-red-400";
    if (line.includes("→"))         return "text-green-300";
    if (line.includes("starting") || line.includes("started")) return "text-indigo-300";
    return "text-slate-300";
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">⚙️ System</h1>
          <p className="text-slate-400 text-sm mt-1">Worker health, live logs, and job controls.</p>
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
            onClick={() => { fetchStatus(); fetchLogs(); }}
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
        !worker.started_at  ? "border-yellow-500/20 bg-yellow-500/5" :
        workerRunning       ? "border-green-500/20 bg-green-500/5"   :
                              "border-red-500/20   bg-red-500/5"
      }`}>
        <div className="flex items-center gap-3">
          <div className={`w-3 h-3 rounded-full ${workerRunning ? "bg-green-400 animate-pulse" : "bg-slate-500"}`} />
          <div>
            <p className="font-semibold text-white">
              {!worker.started_at ? "Worker not running" : workerRunning ? "Worker running" : "Worker stopped"}
            </p>
            {worker.started_at && (
              <p className="text-xs text-slate-400 mt-0.5">
                Started: {String(worker.started_at)} · PID: {String(worker.pid ?? "—")}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* Job cards */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {JOBS.map(({ id, icon, label, interval, fields }) => {
          const job = jobs[id] ?? {};
          const isTriggering = triggering === id;
          return (
            <div key={id} className="rounded-2xl border border-white/8 bg-white/3 px-5 py-4 space-y-3">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm font-semibold text-slate-200">{icon} {label}</p>
                  <p className="text-xs text-slate-500">every {interval}</p>
                </div>
                <button
                  onClick={() => triggerJob(id)}
                  disabled={isTriggering || !workerRunning}
                  title="Run now"
                  className="px-2 py-1 rounded-lg text-xs bg-indigo-500/20 hover:bg-indigo-500/30 text-indigo-300 border border-indigo-500/20 transition disabled:opacity-40"
                >
                  {isTriggering ? "⏳" : "▶ Run"}
                </button>
              </div>
              <p className="text-xs text-slate-500">Last: {String(job.at ?? "—").replace("T", " ").slice(0, 19)}</p>
              <div className="space-y-1">
                {fields.map((f) => (
                  <div key={f} className="flex justify-between text-xs">
                    <span className="text-slate-500 capitalize">{f.replace("_", " ")}</span>
                    <span className="text-white font-medium">{String(job[f] ?? 0)}</span>
                  </div>
                ))}
                {job.error && (
                  <p className="text-xs text-red-400 mt-1 truncate" title={String(job.error)}>
                    {String(job.error).slice(0, 60)}
                  </p>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Live log console */}
      <div>
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Live Logs</h2>
          <span className="text-xs text-slate-600">{logs.length} lines</span>
        </div>
        <div
          ref={logRef}
          className="rounded-2xl bg-black/60 border border-white/8 p-4 h-80 overflow-y-auto font-mono text-xs space-y-0.5"
        >
          {logs.length === 0 ? (
            <p className="text-slate-600">No logs yet — worker starts on API boot.</p>
          ) : (
            logs.map((line, i) => (
              <p key={i} className={logColor(line)}>{line}</p>
            ))
          )}
        </div>
      </div>

      {/* Queue depth */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Send Queue</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {(["pending", "sent", "skipped", "failed"] as const).map((s) => (
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
            { key: "openai",   label: "OPENAI_API_KEY",       desc: "AI features (optional)" },
            { key: "supabase", label: "SUPABASE_JWT_SECRET",  desc: "API authentication" },
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
