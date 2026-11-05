"use client";

import { api, type ThreadItem, type ThreadMessage } from "@/lib/api";
import {
  CornerDownLeft,
  Inbox,
  Loader2,
  MessageSquare,
  RefreshCw,
  Send,
  Sparkles,
  User,
} from "lucide-react";
import { useEffect, useState } from "react";

type Draft = Record<string, unknown> & { id?: number };

export default function RepliesPage() {
  const [threads, setThreads] = useState<ThreadItem[]>([]);
  const [replyFilter, setReplyFilter] = useState<"all" | "needs_info">("all");
  const [selected, setSelected] = useState<ThreadItem | null>(null);
  const [messages, setMessages] = useState<ThreadMessage[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [draftBusy, setDraftBusy] = useState(false);
  const [sendBusy, setSendBusy] = useState(false);
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [sentMsg, setSentMsg] = useState("");

  async function loadThreads() {
    try {
      const items = await api.outreach.threads.list(undefined, 200);
      setThreads(items);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  async function loadDetail(t: ThreadItem) {
    setSelected(t);
    setBusy(`load_${t.id}`);
    try {
      const detail = await api.outreach.threads.get(t.id);
      setMessages(detail.messages ?? []);
      setDraft((detail.pending_draft as Draft | null) ?? null);
      if (detail.pending_draft) {
        const pd = detail.pending_draft as Draft;
        setEditSubject(String(pd.draft_subject ?? ""));
        setEditBody(String(pd.draft_body ?? ""));
      } else {
        setEditSubject("");
        setEditBody("");
      }
      setError("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function generateDraft() {
    if (!selected || selected.last_intent !== "needs_info") return;
    setDraftBusy(true);
    setError("");
    try {
      await api.outreach.threads.generateDraft(selected.id);
      await loadDetail(selected);
      setSentMsg("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDraftBusy(false);
    }
  }

  async function regenerateDraft() {
    if (!selected || !draft?.id) return;
    setDraftBusy(true);
    setError("");
    try {
      await api.outreach.threads.regenerateDraft(selected.id, draft.id as number);
      await loadDetail(selected);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDraftBusy(false);
    }
  }

  async function sendDraft() {
    if (!selected || !draft?.id) return;
    setSendBusy(true);
    setError("");
    try {
      const res = await api.outreach.threads.sendDraft(selected.id, draft.id as number, {
        subject: editSubject,
        body: editBody,
        draft_id: draft.id as number,
      });
      setSentMsg(
        res?.queued
          ? "Queued for delivery through the outbox."
          : "Sent - thread moved out of the needs-reply queue."
      );
      setDraft(null);
      await loadThreads();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSendBusy(false);
    }
  }

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const items = await api.outreach.threads.list(undefined, 200);
        if (active) {
          setThreads(items);
          setError("");
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const intentColor: Record<string, string> = {
    needs_info: "bg-yellow-900/40 text-yellow-300",
    interested: "bg-green-900/40 text-green-300",
    bad_timing: "bg-amber-900/40 text-amber-300",
    other: "bg-slate-700 text-slate-300",
    bounce: "bg-red-900/30 text-red-400",
    auto_reply: "bg-blue-900/40 text-blue-300",
  };
  const replies = threads.filter(
    (thread) => thread.inbound_count > 0 &&
      (replyFilter === "all" || thread.last_intent === "needs_info")
  );
  const needsReplyCount = threads.filter(
    (thread) => thread.inbound_count > 0 && thread.last_intent === "needs_info"
  ).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-white">
            <Inbox className="text-indigo-400" size={24} /> Replies
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            All matched incoming email replies and their classifications.
          </p>
        </div>
        <button
          onClick={loadThreads}
          className="px-4 py-2 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 text-slate-300 text-sm transition flex items-center gap-2"
        >
          <RefreshCw size={14} /> Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-sm">
          {error.includes("fetch") ? "Cannot reach the API — is FastAPI running?" : error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        {/* Conversation list */}
        <div className="rounded-2xl border border-white/8 bg-white/3 overflow-hidden">
          <div className="px-5 py-4 border-b border-white/5 flex items-center justify-between">
            <p className="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <MessageSquare size={14} className="text-indigo-400" /> Replies
            </p>
            <div className="flex items-center gap-3">
              <button
                onClick={() => setReplyFilter("all")}
                aria-pressed={replyFilter === "all"}
                className={`text-xs ${replyFilter === "all" ? "text-indigo-300" : "text-slate-500 hover:text-slate-300"}`}
              >
                All ({threads.filter((thread) => thread.inbound_count > 0).length})
              </button>
              <button
                onClick={() => setReplyFilter("needs_info")}
                aria-pressed={replyFilter === "needs_info"}
                className={`text-xs ${replyFilter === "needs_info" ? "text-indigo-300" : "text-slate-500 hover:text-slate-300"}`}
              >
                Needs a reply ({needsReplyCount})
              </button>
            </div>
          </div>
          <div className="divide-y divide-white/5">
            {loading ? (
              <div className="flex items-center gap-2 px-5 py-8 text-slate-400 text-sm">
                <Loader2 size={16} className="animate-spin" /> Loading conversations…
              </div>
            ) : replies.length === 0 ? (
              <p className="px-5 py-8 text-slate-500 text-sm">
                {replyFilter === "all"
                  ? "No matched incoming replies have been received yet."
                  : "No replies currently need a response."}
              </p>
            ) : (
              replies.map((t) => (
                <button
                  key={t.id}
                  onClick={() => loadDetail(t)}
                  className={`w-full text-left px-5 py-4 transition ${
                    selected?.id === t.id ? "bg-indigo-500/10" : "hover:bg-white/5"
                  }`}
                >
                  <div className="flex items-center justify-between gap-3 mb-1">
                    <p className="font-semibold text-white text-sm truncate">
                      {t.business_name || t.email || "Unknown lead"}
                    </p>
                    {t.last_intent && (
                      <span
                        className={`text-[10px] px-2 py-0.5 rounded-full shrink-0 ${
                          intentColor[t.last_intent] ?? "bg-slate-700 text-slate-300"
                        }`}
                      >
                        {t.last_intent.replace("_", " ")}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-slate-400 truncate mb-1">
                    {t.last_inbound_subject || t.subject || "No subject"}
                  </p>
                  <p className="text-xs text-slate-500 truncate">
                    {t.last_inbound_body || "…"}
                  </p>
                  <div className="flex items-center gap-3 mt-2">
                    <span className="text-[11px] text-slate-500 flex items-center gap-1">
                      <User size={11} /> {t.email || "—"}
                    </span>
                    {t.pending_drafts > 0 && (
                      <span className="text-[11px] text-indigo-300 flex items-center gap-1">
                        <Sparkles size={11} /> draft ready
                      </span>
                    )}
                  </div>
                </button>
              ))
            )}
          </div>
        </div>

        {/* Thread + draft pane */}
        <div className="rounded-2xl border border-white/8 bg-white/3 overflow-hidden">
          {!selected ? (
            <div className="flex flex-col items-center justify-center gap-2 px-6 py-16 text-slate-500 text-sm">
              <MessageSquare size={28} className="text-slate-600" />
              Select a conversation to read it and draft a reply.
            </div>
          ) : (
            <>
              {/* Thread header */}
              <div className="px-5 py-4 border-b border-white/5">
                <div className="flex items-center justify-between gap-3">
                  <p className="font-semibold text-white">{selected.business_name || selected.email}</p>
                  <span className="text-xs text-slate-500">
                    {selected.email} {selected.city ? `· ${selected.city}` : ""}
                  </span>
                </div>
                <p className="text-sm text-slate-300 mt-1 truncate">
                  {selected.last_inbound_subject || selected.subject}
                </p>
                {selected.goal && (
                  <p className="text-xs text-slate-500 mt-1">Goal: {selected.goal}</p>
                )}
              </div>

              {/* Messages */}
              <div className="px-5 py-4 space-y-3 max-h-[380px] overflow-y-auto">
                {busy === `load_${selected.id}` ? (
                  <div className="flex items-center gap-2 text-slate-400 text-sm">
                    <Loader2 size={15} className="animate-spin" /> Loading thread…
                  </div>
                ) : messages.length === 0 ? (
                  <p className="text-slate-500 text-sm">No messages in this thread yet.</p>
                ) : (
                  messages.map((m) => (
                    <div
                      key={m.id}
                      className={`rounded-xl border px-4 py-3 text-sm ${
                        m.direction === "inbound"
                          ? "border-white/8 bg-white/4 text-slate-200"
                          : "border-indigo-500/20 bg-indigo-500/8 text-indigo-100"
                      }`}
                    >
                      <div className="flex items-center justify-between mb-1">
                        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
                          {m.direction === "inbound" ? "They said" : "We sent"}
                        </span>
                        <span className="text-[10px] text-slate-500">
                          {String(m.created_at).replace("T", " ").slice(0, 16)}
                        </span>
                      </div>
                      {m.subject && <p className="text-xs text-slate-400 mb-1">{m.subject}</p>}
                      <p className="whitespace-pre-wrap">{m.body || ""}</p>
                    </div>
                  ))
                )}
              </div>

              {/* Draft area */}
              <div className="border-t border-white/5 px-5 py-4 space-y-3">
                {selected.last_intent !== "needs_info" ? (
                  <p className="text-sm text-slate-500">
                    This reply is available for review. Drafting is reserved for replies classified as needing information.
                  </p>
                ) : !draft ? (
                  <button
                    onClick={generateDraft}
                    disabled={draftBusy || messages.length === 0}
                    className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium bg-indigo-600 hover:bg-indigo-500 text-white transition disabled:opacity-40"
                  >
                    {draftBusy ? (
                      <Loader2 size={15} className="animate-spin" />
                    ) : (
                      <Sparkles size={15} />
                    )}
                    Generate draft with Gemini
                  </button>
                ) : (
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        {draft.detected_price !== null && draft.detected_price !== undefined ? (
                          <span className="px-2 py-0.5 rounded-full bg-white/8">
                            Price: {String(draft.detected_price)}
                          </span>
                        ) : null}
                        {draft.suggested_action ? (
                          <span className="px-2 py-0.5 rounded-full bg-white/8">
                            {String(draft.suggested_action)}
                          </span>
                        ) : null}
                      </div>
                      <button
                        onClick={regenerateDraft}
                        disabled={draftBusy}
                        className="flex items-center gap-1 text-xs text-indigo-300 hover:text-indigo-200 disabled:opacity-40"
                      >
                        <RefreshCw size={12} className={draftBusy ? "animate-spin" : ""} /> Regenerate
                      </button>
                    </div>

                    {draft.reasoning ? (
                      <p className="text-xs text-slate-500 bg-white/4 rounded-lg px-3 py-2">
                        {String(draft.reasoning)}
                      </p>
                    ) : null}

                    <input
                      value={editSubject}
                      onChange={(e) => setEditSubject(e.target.value)}
                      placeholder="Subject"
                      className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-indigo-500/50"
                    />
                    <textarea
                      value={editBody}
                      onChange={(e) => setEditBody(e.target.value)}
                      placeholder="Reply body"
                      rows={8}
                      className="w-full rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-indigo-500/50 resize-y"
                    />

                    <button
                      onClick={sendDraft}
                      disabled={sendBusy || !editSubject.trim() || !editBody.trim()}
                      className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium bg-green-600 hover:bg-green-500 text-white transition disabled:opacity-40"
                    >
                      {sendBusy ? <Loader2 size={15} className="animate-spin" /> : <Send size={15} />}
                      Approve & send
                    </button>
                    {sentMsg && <p className="text-xs text-green-300">{sentMsg}</p>}
                  </div>
                )}
              </div>
              <div className="flex items-center gap-1.5 px-5 pb-4 text-[11px] text-slate-600">
                <CornerDownLeft size={12} /> Sending uses the same SMTP as outreach; the human-in-the-loop approval is the only gate.
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}