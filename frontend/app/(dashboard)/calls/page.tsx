"use client";

import { api, type Lead } from "@/lib/api";
import { useCallback, useEffect, useState } from "react";

interface Call {
  id: number;
  lead_id: number;
  vapi_call_id: string | null;
  status: string;
  duration_sec: number | null;
  summary: string | null;
  transcript: string | null;
  recording_url: string | null;
  qualified: string | null;
  notes: string | null;
  ended_reason: string | null;
  scheduled_at: string;
  initiated_at: string | null;
  ended_at: string | null;
  created_at: string;
  business_name: string | null;
  phone: string | null;
  city: string | null;
  country: string | null;
}

const STATUS_COLOR: Record<string, string> = {
  queued:     "bg-slate-700 text-slate-300",
  initiated:  "bg-blue-900/50 text-blue-300",
  "in-progress": "bg-yellow-900/50 text-yellow-300",
  completed:  "bg-green-900/50 text-green-300",
  failed:     "bg-red-900/30 text-red-400",
};

const QUALIFIED_COLOR: Record<string, string> = {
  yes:   "text-green-400",
  no:    "text-red-400",
  maybe: "text-yellow-400",
};

export default function CallsPage() {
  const [calls, setCalls]       = useState<Call[]>([]);
  const [leads, setLeads]       = useState<Lead[]>([]);
  const [summary, setSummary]   = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<Call | null>(null);
  const [loading, setLoading]   = useState(true);
  const [queueing, setQueueing] = useState(false);
  const [error, setError]       = useState("");
  const [selectedLeads, setSelectedLeads] = useState<Set<number>>(new Set());

  const fetchCalls = useCallback(async () => {
    setLoading(true);
    try {
      const [c, s] = await Promise.all([
        api.calls.list(),
        api.calls.summary(),
      ]);
      setCalls(c.items);
      setSummary(s);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchCallableLeads = useCallback(async () => {
    const res = await api.leads.list({ bucket: "call", limit: "200" });
    setLeads(res.items);
  }, []);

  useEffect(() => {
    fetchCalls();
    fetchCallableLeads();
  }, [fetchCalls, fetchCallableLeads]);

  async function queueSelected() {
    if (!selectedLeads.size) return;
    setQueueing(true);
    try {
      const res = await api.calls.queue(Array.from(selectedLeads));
      setSelectedLeads(new Set());
      await fetchCalls();
      alert(`Queued ${res.queued} call(s). Skipped ${res.skipped} (no phone).`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setQueueing(false);
    }
  }

  function toggleLead(id: number) {
    setSelectedLeads(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  function fmtDuration(sec: number | null) {
    if (!sec) return "—";
    const m = Math.floor(sec / 60), s = sec % 60;
    return m > 0 ? `${m}m ${s}s` : `${s}s`;
  }

  const vapiConfigured = true; // shown regardless — backend checks the key

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-white">📞 Voice Calls</h1>
        <p className="text-slate-400 text-sm mt-1">
          AI-powered outbound qualification calls via Vapi.ai.
        </p>
      </div>

      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-sm">{error}</div>
      )}

      {/* Summary cards */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {["queued","initiated","in-progress","completed","failed"].map(s => (
          <div key={s} className="rounded-xl border border-white/8 bg-white/3 px-4 py-4">
            <p className="text-xs text-slate-400 capitalize mb-1">{s}</p>
            <p className={`text-2xl font-bold ${s === "failed" && (summary[s]??0) > 0 ? "text-red-400" : "text-white"}`}>
              {summary[s] ?? 0}
            </p>
          </div>
        ))}
      </div>

      {/* Queue leads */}
      <div className="rounded-2xl border border-white/8 bg-white/3 p-5 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold text-white">Queue Leads for Calling</h2>
            <p className="text-xs text-slate-500 mt-0.5">Select leads with phone numbers — worker dispatches calls automatically every minute.</p>
          </div>
          <button
            onClick={queueSelected}
            disabled={!selectedLeads.size || queueing}
            className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-medium transition disabled:opacity-40"
          >
            {queueing ? "Queueing…" : `📞 Queue ${selectedLeads.size} call${selectedLeads.size !== 1 ? "s" : ""}`}
          </button>
        </div>

        <div className="max-h-56 overflow-y-auto rounded-xl border border-white/8 divide-y divide-white/5">
          {leads.length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate-500 text-center">No callable leads found. Run a scrape first.</p>
          ) : leads.map(lead => (
            <label key={lead.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-white/5 cursor-pointer">
              <input
                type="checkbox"
                checked={selectedLeads.has(lead.id)}
                onChange={() => toggleLead(lead.id)}
                className="accent-indigo-500"
              />
              <span className="text-sm text-white flex-1 truncate">{lead.business_name || "—"}</span>
              <span className="text-xs text-green-400 font-mono">{lead.phone}</span>
              <span className="text-xs text-slate-500">{lead.city}</span>
            </label>
          ))}
        </div>

        {leads.length > 0 && (
          <div className="flex gap-2 text-xs">
            <button onClick={() => setSelectedLeads(new Set(leads.map(l => l.id)))}
              className="text-indigo-400 hover:text-indigo-300">Select all</button>
            <span className="text-slate-600">·</span>
            <button onClick={() => setSelectedLeads(new Set())}
              className="text-slate-400 hover:text-white">Clear</button>
          </div>
        )}
      </div>

      {/* Calls table */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">Call History</h2>
          <button onClick={fetchCalls} className="text-xs text-slate-500 hover:text-white transition">Refresh</button>
        </div>

        <div className="rounded-2xl border border-white/8 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10">
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Business</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Phone</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Status</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Qualified</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Duration</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Summary</th>
                  <th className="text-left px-4 py-3 text-slate-400 font-medium">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {loading ? (
                  [...Array(5)].map((_, i) => (
                    <tr key={i}>
                      {[...Array(7)].map((_, j) => (
                        <td key={j} className="px-4 py-3">
                          <div className="h-4 bg-white/5 rounded animate-pulse" />
                        </td>
                      ))}
                    </tr>
                  ))
                ) : calls.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="px-4 py-12 text-center text-slate-500">
                      No calls yet. Queue some leads above to get started.
                    </td>
                  </tr>
                ) : calls.map(call => (
                  <tr key={call.id} className="hover:bg-white/3 transition-colors">
                    <td className="px-4 py-3 text-white font-medium truncate max-w-[160px]">
                      {call.business_name || "—"}
                    </td>
                    <td className="px-4 py-3 text-green-400 font-mono text-xs">{call.phone || "—"}</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLOR[call.status] ?? "bg-slate-700 text-slate-300"}`}>
                        {call.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm font-medium">
                      {call.qualified ? (
                        <span className={QUALIFIED_COLOR[call.qualified] ?? "text-slate-400"}>
                          {call.qualified === "yes" ? "✅ Yes" : call.qualified === "no" ? "❌ No" : "🤔 Maybe"}
                        </span>
                      ) : <span className="text-slate-600">—</span>}
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-xs">{fmtDuration(call.duration_sec)}</td>
                    <td className="px-4 py-3 text-slate-400 text-xs max-w-[200px] truncate" title={call.summary ?? ""}>
                      {call.summary || "—"}
                    </td>
                    <td className="px-4 py-3">
                      <button
                        onClick={() => setSelected(call)}
                        className="text-xs text-indigo-400 hover:text-indigo-300 underline"
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Detail modal */}
      {selected && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={() => setSelected(null)}>
          <div className="bg-[#1a1d2e] border border-white/10 rounded-2xl p-6 max-w-2xl w-full max-h-[80vh] overflow-y-auto space-y-4"
            onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-bold text-white">{selected.business_name}</h3>
              <button onClick={() => setSelected(null)} className="text-slate-400 hover:text-white text-xl">×</button>
            </div>

            <div className="grid grid-cols-2 gap-3 text-sm">
              {[
                ["Phone",    selected.phone],
                ["Status",   selected.status],
                ["Qualified",selected.qualified ?? "—"],
                ["Duration", fmtDuration(selected.duration_sec)],
                ["Ended reason", selected.ended_reason ?? "—"],
                ["Vapi ID",  selected.vapi_call_id ?? "—"],
              ].map(([k, v]) => (
                <div key={k} className="bg-white/5 rounded-lg px-3 py-2">
                  <p className="text-xs text-slate-500">{k}</p>
                  <p className="text-white font-medium truncate">{v}</p>
                </div>
              ))}
            </div>

            {selected.summary && (
              <div className="bg-white/5 rounded-xl px-4 py-3">
                <p className="text-xs text-slate-500 mb-1">Summary</p>
                <p className="text-sm text-slate-200">{selected.summary}</p>
              </div>
            )}

            {selected.notes && (
              <div className="bg-white/5 rounded-xl px-4 py-3">
                <p className="text-xs text-slate-500 mb-1">AI Notes</p>
                <p className="text-sm text-slate-200">{selected.notes}</p>
              </div>
            )}

            {selected.transcript && (
              <div className="bg-black/40 rounded-xl px-4 py-3">
                <p className="text-xs text-slate-500 mb-2">Transcript</p>
                <pre className="text-xs text-slate-300 whitespace-pre-wrap font-mono leading-relaxed max-h-64 overflow-y-auto">
                  {selected.transcript}
                </pre>
              </div>
            )}

            {selected.recording_url && (
              <div>
                <p className="text-xs text-slate-500 mb-2">Recording</p>
                <audio controls src={selected.recording_url} className="w-full" />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
