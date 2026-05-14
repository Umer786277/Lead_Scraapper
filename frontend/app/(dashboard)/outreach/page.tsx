"use client";

import { api } from "@/lib/api";
import { useEffect, useState } from "react";

export default function OutreachPage() {
  const [campaigns, setCampaigns] = useState<Record<string, unknown>[]>([]);
  const [queue, setQueue] = useState<Record<string, number>>({});
  const [templates, setTemplates] = useState<{ id: number; name: string; subject: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<Record<string, number> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([api.outreach.campaigns(), api.outreach.queue(), api.outreach.templates()])
      .then(([c, q, t]) => {
        setCampaigns(c as typeof campaigns);
        setQueue(q as typeof queue);
        setTemplates(t as typeof templates);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  async function triggerSend(dryRun: boolean) {
    setSending(true); setSendResult(null);
    try {
      const res = await api.outreach.send(dryRun);
      setSendResult(res as Record<string, number>);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold text-white">✉️ Outreach</h1>
        <p className="text-slate-400 text-sm mt-1">Campaigns, templates, and the send queue.</p>
      </div>

      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-sm">
          {error.includes("fetch") ? "Cannot reach the API — is FastAPI running?" : error}
        </div>
      )}

      {/* Queue status */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">Send queue</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          {(["pending", "sent", "failed", "skipped"] as const).map((s) => (
            <div key={s} className="rounded-xl border border-white/8 bg-white/3 px-4 py-4">
              <p className="text-xs text-slate-400 mb-1 capitalize">{s}</p>
              <p className="text-2xl font-bold text-white">{queue[s] ?? 0}</p>
            </div>
          ))}
        </div>
        <div className="flex gap-3">
          <button
            onClick={() => triggerSend(false)}
            disabled={sending || !queue.pending}
            className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-semibold transition"
          >
            {sending ? "Sending…" : `Send ${queue.pending ?? 0} pending`}
          </button>
          <button
            onClick={() => triggerSend(true)}
            disabled={sending}
            className="px-5 py-2.5 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 disabled:opacity-40 text-slate-300 text-sm transition"
          >
            Dry run (preview only)
          </button>
        </div>

        {sendResult && (
          <div className="mt-3 rounded-xl bg-green-500/10 border border-green-500/20 px-4 py-3 text-green-400 text-sm">
            Sent: {sendResult.sent} · Failed: {sendResult.failed} · Skipped: {sendResult.skipped}
          </div>
        )}
      </div>

      {/* Campaigns */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Campaigns ({campaigns.length})
        </h2>
        {loading ? (
          <div className="h-32 rounded-2xl bg-white/5 animate-pulse" />
        ) : campaigns.length === 0 ? (
          <div className="rounded-2xl border border-white/8 bg-white/3 px-6 py-10 text-center text-slate-500">
            No campaigns yet. Create one from the Leads page after extracting emails.
          </div>
        ) : (
          <div className="rounded-2xl border border-white/8 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/8 bg-white/3">
                  {["Name", "Status", "Leads", "Sent", "Pending"].map((h) => (
                    <th key={h} className="text-left px-5 py-3 text-slate-400 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {campaigns.map((c: Record<string, unknown>) => (
                  <tr key={String(c.id)} className="hover:bg-white/3 transition-colors">
                    <td className="px-5 py-3 text-white font-medium">{String(c.name)}</td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                        c.status === "active" ? "bg-green-900/50 text-green-300" : "bg-slate-700 text-slate-400"
                      }`}>{String(c.status)}</span>
                    </td>
                    <td className="px-5 py-3 text-slate-300">{String(c.leads_count)}</td>
                    <td className="px-5 py-3 text-green-400">{String(c.sent_count ?? 0)}</td>
                    <td className="px-5 py-3 text-yellow-400">{String(c.pending_count ?? 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Templates */}
      <div>
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-3">
          Email templates ({templates.length})
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {templates.map((t) => (
            <div key={t.id} className="rounded-xl border border-white/8 bg-white/3 px-5 py-4">
              <p className="font-medium text-white mb-1">{t.name}</p>
              <p className="text-xs text-slate-400 truncate">{t.subject}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
