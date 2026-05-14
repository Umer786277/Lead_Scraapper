"use client";

import { api } from "@/lib/api";
import { useEffect, useState } from "react";

export default function SystemPage() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");

  function fetchStatus() {
    api.system.status()
      .then((d) => setData(d))
      .catch((e) => setError((e as Error).message));
  }

  useEffect(() => { fetchStatus(); }, []);

  const worker = data?.worker ?? {};
  const queue  = data?.queue  ?? {};
  const config = data?.config ?? {};
  const jobs   = worker?.jobs ?? {};

  const workerRunning = !worker.stopped_at && !!worker.started_at;

  return (
    <div className="space-y-8">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">⚙️ System</h1>
          <p className="text-slate-400 text-sm mt-1">Worker health, queue depth, and configuration.</p>
        </div>
        <button
          onClick={fetchStatus}
          className="px-4 py-2 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 text-slate-300 text-sm transition"
        >
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-sm">
          {error.includes("fetch") ? "Cannot reach the API — is FastAPI running on port 8000?" : error}
        </div>
      )}

      {/* Worker status */}
      <div className={`rounded-2xl border px-6 py-5 ${
        !worker.started_at ? "border-yellow-500/20 bg-yellow-500/5" :
        workerRunning ? "border-green-500/20 bg-green-500/5" :
        "border-red-500/20 bg-red-500/5"
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
        {!worker.started_at && (
          <div className="mt-3 font-mono text-xs bg-black/30 rounded-lg px-4 py-3 text-slate-300">
            python worker.py
          </div>
        )}
      </div>

      {/* Job stats */}
      {worker.started_at && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {[
            { key: "enrich", icon: "🔍", label: "Enrich (30 s)",    fields: ["found", "enriched", "enrolled"] },
            { key: "send",   icon: "📤", label: "Send queue (60 s)", fields: ["sent", "failed", "skipped"] },
            { key: "inbox",  icon: "📥", label: "Inbox (10 min)",    fields: ["replies"] },
          ].map(({ key, icon, label, fields }) => {
            const job = jobs[key] ?? {};
            return (
              <div key={key} className="rounded-2xl border border-white/8 bg-white/3 px-5 py-5">
                <p className="text-sm font-semibold text-slate-300 mb-1">{icon} {label}</p>
                <p className="text-xs text-slate-500 mb-3">Last run: {String(job.at ?? "—")}</p>
                <div className="space-y-1">
                  {fields.map((f) => (
                    <div key={f} className="flex justify-between text-sm">
                      <span className="text-slate-400 capitalize">{f}</span>
                      <span className="text-white font-medium">{String(job[f] ?? 0)}</span>
                    </div>
                  ))}
                  {job.error && <p className="text-xs text-red-400 mt-2">{String(job.error)}</p>}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Queue depth */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Send queue</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {(["pending", "sent", "skipped", "failed"] as const).map((s) => (
            <div key={s} className="rounded-xl border border-white/8 bg-white/3 px-4 py-4">
              <p className="text-xs text-slate-400 capitalize mb-1">{s}</p>
              <p className="text-2xl font-bold text-white">{queue[s] ?? 0}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Config checklist */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Configuration</h2>
        <div className="rounded-2xl border border-white/8 overflow-hidden divide-y divide-white/5">
          {[
            { key: "database", label: "DATABASE_URL",    desc: "PostgreSQL / Supabase" },
            { key: "smtp",     label: "SMTP_HOST",       desc: "Email sending" },
            { key: "imap",     label: "IMAP_HOST",       desc: "Reply detection (optional)" },
            { key: "openai",   label: "OPENAI_API_KEY",  desc: "AI negotiation drafts (optional)" },
            { key: "supabase", label: "SUPABASE_JWT_SECRET", desc: "API authentication" },
          ].map(({ key, label, desc }) => {
            const ok = config[key];
            return (
              <div key={key} className="flex items-center justify-between px-5 py-3">
                <div>
                  <span className="text-sm text-white font-mono">{label}</span>
                  <span className="text-xs text-slate-500 ml-2">{desc}</span>
                </div>
                <span className={`text-xs font-medium px-2 py-0.5 rounded-full ${ok ? "bg-green-900/50 text-green-400" : "bg-red-900/30 text-red-400"}`}>
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
