"use client";

import { api, type EmailAccount } from "@/lib/api";
import { Mail } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import Pagination from "@/components/Pagination";

const SENDS_PAGE_SIZE = 25;

type SendRow = {
  id: number;
  status: string;
  step_number: number;
  scheduled_at: string;
  sent_at: string | null;
  error: string | null;
  lead_id: number;
  business_name: string | null;
  email: string | null;
  campaign_name: string;
};

type SendResult = {
  queued?: number;
  sent: number;
  failed: number;
  skipped: number;
  deferred?: number;
  retry?: number;
  unknown?: number;
  paused?: boolean;
  limit_reached?: boolean;
  error?: string;
};

const SEND_STATUS_COLORS: Record<string, string> = {
  pending: "bg-slate-700 text-slate-300",
  sent:    "bg-green-900/50 text-green-300",
  failed:  "bg-red-900/30 text-red-400",
  skipped: "bg-yellow-900/40 text-yellow-300",
};

export default function OutreachPage() {
  const [campaigns, setCampaigns] = useState<Record<string, unknown>[]>([]);
  const [queue, setQueue] = useState<Record<string, number>>({});
  const [templates, setTemplates] = useState<{ id: number; name: string; subject: string }[]>([]);
  const [sends, setSends] = useState<SendRow[]>([]);
  const [sendsTotal, setSendsTotal] = useState(0);
  const [sendsPage, setSendsPage] = useState(1);
  const [sendsFilter, setSendsFilter] = useState<string | undefined>(undefined);
  const [sendsLoading, setSendsLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<SendResult | null>(null);
  const [error, setError] = useState("");
  const [batchSize, setBatchSize] = useState(20);
  const [limits, setLimits] = useState<{
    max_per_day: number; max_per_hour: number;
    max_emails_per_minute: number;
    hourly_allocation_limit: number;
    send_max_per_domain_per_hour: number;
    send_jitter_max_seconds: number;
    effective_hourly_allocation_limit: number;
    sent_today: number; sent_this_hour: number; paused: boolean;
    paused_until: string | null; pause_reason: string | null; pause_active: boolean;
  } | null>(null);
  const [toggleBusy, setToggleBusy] = useState(false);
  const [rateLimitInput, setRateLimitInput] = useState(60);
  const [rateLimitBusy, setRateLimitBusy] = useState(false);
  const [rateLimitMessage, setRateLimitMessage] = useState("");
  const [hourlyLimitInput, setHourlyLimitInput] = useState(60);
  const [hourlyLimitBusy, setHourlyLimitBusy] = useState(false);
  const [hourlyLimitMessage, setHourlyLimitMessage] = useState("");
  const [domainLimitInput, setDomainLimitInput] = useState(10);
  const [domainLimitBusy, setDomainLimitBusy] = useState(false);
  const [domainLimitMessage, setDomainLimitMessage] = useState("");
  const [jitterInput, setJitterInput] = useState(0);
  const [jitterBusy, setJitterBusy] = useState(false);
  const [jitterMessage, setJitterMessage] = useState("");
  const [accounts, setAccounts] = useState<EmailAccount[]>([]);
  const [canManageAccounts, setCanManageAccounts] = useState(false);
  const [showAccountForm, setShowAccountForm] = useState(false);
  const [editingAccountId, setEditingAccountId] = useState<number | null>(null);
  const [accountBusy, setAccountBusy] = useState(false);
  const [accountMsg, setAccountMsg] = useState("");
  const [accountForm, setAccountForm] = useState({
    label: "", from_name: "", from_email: "", reply_to: "",
    smtp_host: "", smtp_port: 587, smtp_user: "", smtp_password: "", smtp_use_tls: true,
    imap_host: "", imap_port: 993, imap_user: "", imap_password: "",
    daily_limit: 50, hourly_limit: 10, warmup_days: 0,
  });

  const loadSends = useCallback((status: string | undefined, page: number) => {
    setSendsLoading(true);
    api.outreach.sends(status, SENDS_PAGE_SIZE, (page - 1) * SENDS_PAGE_SIZE)
      .then((res) => {
        setSends(res.items as SendRow[]);
        setSendsTotal(res.total);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setSendsLoading(false));
  }, []);

  useEffect(() => {
    Promise.all([api.outreach.campaigns(), api.outreach.queue(), api.outreach.templates(), api.outreach.limits()])
      .then(([c, q, t, l]) => {
        setCampaigns(c as typeof campaigns);
        setQueue(q as typeof queue);
        setTemplates(t as typeof templates);
        setLimits(l as typeof limits);
        setRateLimitInput(l.max_emails_per_minute);
        setHourlyLimitInput(l.hourly_allocation_limit);
        setDomainLimitInput(l.send_max_per_domain_per_hour);
        setJitterInput(l.send_jitter_max_seconds ?? 0);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, []);

  const loadAccounts = useCallback(() => {
    api.outreach.accounts()
      .then((res) => setAccounts(res.items))
      .catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => { loadAccounts(); }, [loadAccounts]);

  useEffect(() => {
    api.orgs.members.list()
      .then(({ can_manage }) => setCanManageAccounts(can_manage))
      .catch((e) => setError((e as Error).message));
  }, []);

  async function saveAccount() {
    setAccountBusy(true); setAccountMsg("");
    try {
      const payload = {
        ...accountForm,
        smtp_port: Number(accountForm.smtp_port),
        imap_port: Number(accountForm.imap_port),
        daily_limit: Number(accountForm.daily_limit),
        hourly_limit: Number(accountForm.hourly_limit),
        warmup_days: Number(accountForm.warmup_days),
        ...(accountForm.smtp_password ? {} : { smtp_password: undefined }),
        ...(accountForm.imap_password ? {} : { imap_password: undefined }),
      };
      if (editingAccountId) {
        await api.outreach.updateAccount(editingAccountId, payload);
      } else {
        await api.outreach.createAccount(payload);
      }
      setAccountMsg(editingAccountId ? "Account updated." : "Account added.");
      setShowAccountForm(false);
      setEditingAccountId(null);
      setAccountForm((f) => ({ ...f, from_email: "", smtp_password: "", imap_password: "" }));
      loadAccounts();
    } catch (e) {
      setAccountMsg((e as Error).message);
    } finally {
      setAccountBusy(false);
    }
  }

  function editAccount(account: EmailAccount) {
    setEditingAccountId(account.id);
    setAccountForm({
      label: account.label ?? "",
      from_name: account.from_name ?? "",
      from_email: account.from_email,
      reply_to: account.reply_to ?? "",
      smtp_host: account.smtp_host,
      smtp_port: account.smtp_port,
      smtp_user: account.smtp_user ?? "",
      smtp_password: "",
      smtp_use_tls: account.smtp_use_tls,
      imap_host: account.imap_host ?? "",
      imap_port: account.imap_port,
      imap_user: account.imap_user ?? "",
      imap_password: "",
      daily_limit: account.daily_limit,
      hourly_limit: account.hourly_limit,
      warmup_days: account.warmup_days,
    });
    setShowAccountForm(true);
    setAccountMsg("");
  }

  async function testAccount(id: number) {
    setAccountBusy(true); setAccountMsg("");
    try {
      const res = await api.outreach.testAccount(id);
      setAccountMsg(
        `SMTP ${res.smtp_ok ? "OK" : "FAILED"} · IMAP ${res.imap_ok ? "OK" : "FAILED"}` +
        (res.errors.length ? ` — ${res.errors.join("; ")}` : "")
      );
    } catch (e) {
      setAccountMsg((e as Error).message);
    } finally {
      setAccountBusy(false);
    }
  }

  async function removeAccount(id: number) {
    setAccountBusy(true);
    try {
      await api.outreach.deleteAccount(id);
      loadAccounts();
    } catch (e) {
      setAccountMsg((e as Error).message);
    } finally {
      setAccountBusy(false);
    }
  }

  useEffect(() => {
    let active = true;
    (async () => {
      setSendsLoading(true);
      try {
        const res = await api.outreach.sends(
          sendsFilter,
          SENDS_PAGE_SIZE,
          (sendsPage - 1) * SENDS_PAGE_SIZE
        );
        if (!active) return;
        setSends(res.items as SendRow[]);
        setSendsTotal(res.total);
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        if (active) setSendsLoading(false);
      }
    })();
    return () => { active = false; };
  }, [sendsFilter, sendsPage]);

  async function triggerSend(dryRun: boolean) {
    setSending(true); setSendResult(null);
    try {
      const res = await api.outreach.send(dryRun, batchSize);
      setSendResult(res as SendResult);
      loadSends(sendsFilter, sendsPage);
      const q = await api.outreach.queue();
      setQueue(q as typeof queue);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  }

  async function togglePause() {
    setToggleBusy(true);
    try {
      const next = !(limits?.paused ?? false);
      await api.system.pause(next);
      setLimits((prev) => (prev ? { ...prev, paused: next } : prev));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setToggleBusy(false);
    }
  }

  async function saveRateLimit() {
    setRateLimitBusy(true);
    setRateLimitMessage("");
    try {
      const result = await api.outreach.updateLimits({
        max_emails_per_minute: rateLimitInput,
      });
      setLimits((current) => current
        ? {
          ...current,
          max_emails_per_minute: result.max_emails_per_minute,
          effective_hourly_allocation_limit: Math.min(
            current.hourly_allocation_limit,
            result.max_emails_per_minute * 60,
          ),
        }
        : current);
      setRateLimitInput(result.max_emails_per_minute);
      setRateLimitMessage("Dispatch rate saved.");
    } catch (e) {
      setRateLimitMessage((e as Error).message);
    } finally {
      setRateLimitBusy(false);
    }
  }

  async function saveHourlyAllocationLimit() {
    setHourlyLimitBusy(true);
    setHourlyLimitMessage("");
    try {
      const result = await api.outreach.updateHourlyAllocationLimit({
        hourly_allocation_limit: hourlyLimitInput,
      });
      setLimits((current) => current
        ? {
          ...current,
          hourly_allocation_limit: result.hourly_allocation_limit,
          effective_hourly_allocation_limit: Math.min(
            result.hourly_allocation_limit,
            current.max_emails_per_minute * 60,
          ),
        }
        : current);
      setHourlyLimitInput(result.hourly_allocation_limit);
      setHourlyLimitMessage("Hourly allocation cap saved.");
    } catch (e) {
      setHourlyLimitMessage((e as Error).message);
    } finally {
      setHourlyLimitBusy(false);
    }
  }

  async function saveDomainHourlyLimit() {
    setDomainLimitBusy(true);
    setDomainLimitMessage("");
    try {
      const result = await api.outreach.updateDomainHourlyLimit({
        send_max_per_domain_per_hour: domainLimitInput,
      });
      setLimits((current) => current
        ? { ...current, send_max_per_domain_per_hour: result.send_max_per_domain_per_hour }
        : current);
      setDomainLimitInput(result.send_max_per_domain_per_hour);
      setDomainLimitMessage("Per-domain hourly limit saved.");
    } catch (e) {
      setDomainLimitMessage((e as Error).message);
    } finally {
      setDomainLimitBusy(false);
    }
  }

  async function saveJitter() {
    setJitterBusy(true);
    setJitterMessage("");
    try {
      const result = await api.outreach.updateSendJitter({
        send_jitter_max_seconds: jitterInput,
      });
      setLimits((current) => current
        ? { ...current, send_jitter_max_seconds: result.send_jitter_max_seconds }
        : current);
      setJitterInput(result.send_jitter_max_seconds);
      setJitterMessage("Random send delay saved.");
    } catch (e) {
      setJitterMessage((e as Error).message);
    } finally {
      setJitterBusy(false);
    }
  }

  async function reloadOverview() {
    try {
      const [c, q, l] = await Promise.all([
        api.outreach.campaigns(),
        api.outreach.queue(),
        api.outreach.limits(),
      ]);
      setCampaigns(c as typeof campaigns);
      setQueue(q as typeof queue);
      setLimits(l as typeof limits);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  async function toggleCampaignStatus(c: Record<string, unknown>) {
    const next = c.status === "active" ? "paused" : "active";
    try {
      await api.outreach.setCampaignStatus(Number(c.id), next);
      await reloadOverview();
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function filterSends(status?: string) {
    setSendsFilter(status);
    setSendsPage(1);
  }

  const inputCls =
    "w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white " +
    "placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/40";

  return (
    <div className="space-y-8">
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-white">
          <Mail className="text-indigo-400" size={24} /> Outreach
        </h1>
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
        <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-4">
          {(["pending", "sending", "sent", "failed", "skipped"] as const).map((s) => (
            <button
              key={s}
              onClick={() => filterSends(sendsFilter === s ? undefined : s)}
              className={`text-left rounded-xl border px-4 py-4 transition ${
                sendsFilter === s
                  ? "border-indigo-500/50 bg-indigo-500/10"
                  : "border-white/8 bg-white/3 hover:bg-white/5"
              }`}
            >
              <p className="text-xs text-slate-400 mb-1 capitalize">{s}</p>
              <p className="text-2xl font-bold text-white">{queue[s] ?? 0}</p>
            </button>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-slate-400">
            Per round:
            <input
              type="number"
              min={1}
              max={200}
              value={batchSize}
              onChange={(e) => setBatchSize(Math.max(1, Math.min(200, Number(e.target.value) || 1)))}
              className="w-16 bg-white/5 border border-white/10 rounded-lg px-2 py-1.5 text-sm text-white text-center focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />
          </label>
          <button
            onClick={() => triggerSend(false)}
            disabled={sending || !queue.pending}
            className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-semibold transition"
          >
            {sending ? "Queueing..." : `Queue ${Math.min(batchSize, queue.pending ?? 0)} of ${queue.pending ?? 0} pending`}
          </button>
          <button
            onClick={() => triggerSend(true)}
            disabled={sending}
            className="px-5 py-2.5 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 disabled:opacity-40 text-slate-300 text-sm transition"
          >
            Dry run (preview only)
          </button>
          <div className="flex-1" />
          {canManageAccounts && (
            <button
              onClick={togglePause}
              disabled={toggleBusy}
              className={`px-5 py-2.5 rounded-xl text-sm font-semibold transition disabled:opacity-40 ${
                limits?.paused
                  ? "bg-green-600 hover:bg-green-500 text-white"
                  : "border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 text-red-300"
              }`}
            >
              {toggleBusy ? "…" : limits?.paused ? "Resume outreach" : "Pause outreach"}
            </button>
          )}
        </div>

        {sendResult && (
          <div className="mt-3 rounded-xl bg-white/5 border border-white/10 px-4 py-3 text-slate-300 text-sm">
            Queued: {sendResult.queued ?? 0} - Sent: {sendResult.sent} - Failed: {sendResult.failed} - Skipped: {sendResult.skipped}
            {sendResult.deferred ? ` - Deferred: ${sendResult.deferred}` : ""}
            {sendResult.retry ? ` - Retrying: ${sendResult.retry}` : ""}
            {sendResult.unknown ? ` - Unknown: ${sendResult.unknown}` : ""}
            {sendResult.error ? <span className="block mt-1 text-amber-300">{sendResult.error}</span> : null}
          </div>
        )}

        {limits && (
          <div className="mt-3 rounded-xl bg-white/3 border border-white/8 px-4 py-3 text-slate-400 text-xs space-y-1">
            <div className="flex flex-wrap gap-x-6 gap-y-1">
              <span>
                Sent today: <b className="text-white">{limits.sent_today}</b>
                {limits.max_per_day ? (
                  <span className="text-slate-500"> / {limits.max_per_day} cap</span>
                ) : (
                  <span className="text-slate-500"> — unlimited (set SEND_MAX_PER_DAY)</span>
                )}
              </span>
              <span>
                This hour: <b className="text-white">{limits.sent_this_hour}</b>
                {limits.max_per_hour ? (
                  <span className="text-slate-500"> / {limits.max_per_hour} cap</span>
                ) : (
                  <span className="text-slate-500"> — unlimited (set SEND_MAX_PER_HOUR)</span>
                )}
              </span>
              <span className={limits.paused ? "text-amber-400" : "text-green-400"}>
                {limits.paused ? "PAUSED — nothing will send" : "Active"}
              </span>
              <span>
                Dispatch rate: <b className="text-white">{limits.max_emails_per_minute}</b> emails/min
              </span>
              <span>
                Random delay: <b className="text-white">
                  {limits.send_jitter_max_seconds > 0
                    ? `0–${limits.send_jitter_max_seconds}s`
                    : "off"}
                </b>
              </span>
              <span>
                Tenant allocation: <b className="text-white">{limits.hourly_allocation_limit}</b> emails/hour
                <span className="text-slate-500">
                  {" "}· effective {limits.effective_hourly_allocation_limit}/hour
                  {" "}· {(3600 / limits.effective_hourly_allocation_limit).toFixed(2)} sec/slot
                </span>
              </span>
              <span>
                Per recipient domain: <b className="text-white">
                  {limits.send_max_per_domain_per_hour}
                </b> emails/hour
              </span>
              {limits.pause_active && (
                <span className="text-amber-400">
                  Provider pause until {new Date(limits.paused_until!).toLocaleString()}
                  {limits.pause_reason ? ` — ${limits.pause_reason}` : ""}
                </span>
              )}
            </div>
            {canManageAccounts && (
              <div className="flex flex-wrap items-end gap-3 pt-2">
                <label className="text-xs text-slate-400">
                  Maximum emails per recipient domain per hour (1–10,000)
                  <input
                    type="number"
                    min={1}
                    max={10000}
                    step={1}
                    value={domainLimitInput}
                    onChange={(e) => setDomainLimitInput(Number(e.target.value))}
                    className="mt-1 w-36 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                  />
                </label>
                <button
                  onClick={saveDomainHourlyLimit}
                  disabled={domainLimitBusy || domainLimitInput < 1 || domainLimitInput > 10000}
                  className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-semibold transition"
                >
                  {domainLimitBusy ? "Saving…" : "Save domain limit"}
                </button>
                {domainLimitMessage && (
                  <span className="text-xs text-slate-400">{domainLimitMessage}</span>
                )}
              </div>
            )}
            {canManageAccounts && (
              <div className="flex flex-wrap items-end gap-3 pt-2">
                <label className="text-xs text-slate-400">
                  Desired throughput (emails per minute, 1–60)
                  <input
                    type="number"
                    min={1}
                    max={60}
                    step={1}
                    value={rateLimitInput}
                    onChange={(e) => setRateLimitInput(Number(e.target.value))}
                    className="mt-1 w-32 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                  />
                </label>
                <button
                  onClick={saveRateLimit}
                  disabled={rateLimitBusy || rateLimitInput < 1 || rateLimitInput > 60}
                  className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-semibold transition"
                >
                  {rateLimitBusy ? "Saving…" : "Save rate"}
                </button>
                {rateLimitMessage && <span className="text-xs text-slate-400">{rateLimitMessage}</span>}
              </div>
            )}
            {canManageAccounts && (
              <div className="flex flex-wrap items-end gap-3 pt-2">
                <label className="text-xs text-slate-400">
                  Maximum tenant dispatches per hour (1–3600)
                  <input
                    type="number"
                    min={1}
                    max={3600}
                    step={1}
                    value={hourlyLimitInput}
                    onChange={(e) => setHourlyLimitInput(Number(e.target.value))}
                    className="mt-1 w-36 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                  />
                </label>
                <button
                  onClick={saveHourlyAllocationLimit}
                  disabled={hourlyLimitBusy || hourlyLimitInput < 1 || hourlyLimitInput > 3600}
                  className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-semibold transition"
                >
                  {hourlyLimitBusy ? "Saving…" : "Save hourly cap"}
                </button>
                {hourlyLimitMessage && <span className="text-xs text-slate-400">{hourlyLimitMessage}</span>}
              </div>
            )}
            {canManageAccounts && (
              <div className="flex flex-wrap items-end gap-3 pt-2">
                <label className="text-xs text-slate-400">
                  Random delay between emails (max seconds, 0 = off)
                  <input
                    type="number"
                    min={0}
                    max={3600}
                    step={1}
                    value={jitterInput}
                    onChange={(e) => setJitterInput(Number(e.target.value))}
                    className="mt-1 w-36 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
                  />
                </label>
                <button
                  onClick={saveJitter}
                  disabled={jitterBusy || jitterInput < 0 || jitterInput > 3600}
                  className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-xs font-semibold transition"
                >
                  {jitterBusy ? "Saving…" : "Save random delay"}
                </button>
                {jitterMessage && <span className="text-xs text-slate-400">{jitterMessage}</span>}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Sender accounts */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
            Sender accounts ({accounts.length})
          </h2>
          {canManageAccounts && <button
            onClick={() => {
              if (showAccountForm) {
                setShowAccountForm(false);
                setEditingAccountId(null);
              } else {
                setShowAccountForm(true);
              }
              setAccountMsg("");
            }}
            className="text-xs px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition"
          >
            {showAccountForm ? "Cancel" : "Add account"}
          </button>}
        </div>

        {accountMsg && (
          <div className="mb-3 rounded-lg bg-white/5 border border-white/10 px-4 py-2 text-slate-300 text-xs">
            {accountMsg}
          </div>
        )}

        {showAccountForm && (
          <div className="mb-4 rounded-2xl border border-white/8 bg-white/3 p-4 space-y-3">
            <h3 className="text-sm font-semibold text-white">
              {editingAccountId ? "Edit sender account" : "Add sender account"}
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              {([
                ["label", "Label", "Main sender"],
                ["from_name", "From name", "Umer"],
                ["from_email", "From email", "you@company.com"],
                ["reply_to", "Reply-To (optional)", "you@company.com"],
                ["smtp_host", "SMTP host", "smtp.gmail.com"],
                ["smtp_user", "SMTP user", "you@company.com"],
                ["smtp_password", "SMTP password / app password", ""],
                ["imap_host", "IMAP host (optional)", "imap.gmail.com"],
                ["imap_user", "IMAP user", "you@company.com"],
                ["imap_password", "IMAP password", ""],
              ] as const).map(([key, label, placeholder]) => (
                <label key={key} className="text-xs text-slate-400">
                  {label}
                  <input
                    type={key.includes("password") ? "password" : "text"}
                    value={String(accountForm[key as keyof typeof accountForm])}
                    placeholder={placeholder}
                    onChange={(e) => setAccountForm((f) => ({ ...f, [key]: e.target.value }))}
                    className={`mt-1 ${inputCls}`}
                  />
                </label>
              ))}
              {([
                ["smtp_port", "SMTP port"],
                ["imap_port", "IMAP port"],
                ["daily_limit", "Daily limit"],
                ["hourly_limit", "Hourly limit"],
                ["warmup_days", "Warmup days (0 = off)"],
              ] as const).map(([key, label]) => (
                <label key={key} className="text-xs text-slate-400">
                  {label}
                  <input
                    type="number"
                    min={0}
                    value={Number(accountForm[key as keyof typeof accountForm])}
                    onChange={(e) => setAccountForm((f) => ({ ...f, [key]: Number(e.target.value) }))}
                    className={`mt-1 ${inputCls}`}
                  />
                </label>
              ))}
              <label className="text-xs text-slate-400">
                SMTP security
                <select
                  value={accountForm.smtp_use_tls ? "starttls" : "ssl"}
                  onChange={(e) => setAccountForm((f) => ({ ...f, smtp_use_tls: e.target.value === "starttls" }))}
                  className={`mt-1 ${inputCls}`}
                >
                  <option value="starttls">STARTTLS (usually port 587)</option>
                  <option value="ssl">SSL/TLS (usually port 465)</option>
                </select>
              </label>
            </div>
            <button
              onClick={saveAccount}
              disabled={accountBusy || !accountForm.from_email || !accountForm.smtp_host}
              className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-semibold transition"
            >
              {accountBusy ? "Saving…" : editingAccountId ? "Save changes" : "Save account"}
            </button>
          </div>
        )}

        {accounts.length === 0 ? (
          <div className="rounded-2xl border border-white/8 bg-white/3 px-6 py-8 text-center text-slate-500 text-sm">
            No sender accounts yet. Add one to start sending, or keep using the env SMTP fallback.
          </div>
        ) : (
          <div className="rounded-2xl border border-white/8 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/8 bg-white/3">
                    {(canManageAccounts
                      ? ["Sender", "SMTP", "IMAP", "Limits", "Status", "Actions"]
                      : ["Sender", "SMTP", "IMAP", "Limits", "Status"]
                    ).map((h) => (
                      <th key={h} className="text-left px-5 py-3 text-slate-400 font-medium whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {accounts.map((a) => (
                    <tr key={a.id} className="hover:bg-white/3 transition-colors">
                      <td className="px-5 py-3 text-white font-medium whitespace-nowrap">
                        {a.label || a.from_email}
                        <span className="block text-xs text-slate-500 font-normal">{a.from_email}</span>
                      </td>
                      <td className="px-5 py-3 text-slate-400 text-xs">
                        {a.smtp_host}:{a.smtp_port}
                        <span className="block text-slate-500">
                          {a.has_smtp_password ? "credentials saved" : "no password"}
                        </span>
                      </td>
                      <td className="px-5 py-3 text-slate-400 text-xs">
                        {a.imap_host || "—"}
                      </td>
                      <td className="px-5 py-3 text-slate-400 text-xs">
                        {a.daily_limit}/day · {a.hourly_limit}/hr
                        {a.warmup_days ? ` · warmup ${a.warmup_days}d` : ""}
                      </td>
                      <td className="px-5 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                          a.status === "active"
                            ? "bg-green-900/50 text-green-300"
                            : "bg-slate-700 text-slate-400"
                        }`}>{a.status}</span>
                      </td>
                      {canManageAccounts && <td className="px-5 py-3 whitespace-nowrap space-x-2">
                        <button
                          onClick={() => editAccount(a)}
                          disabled={accountBusy}
                          className="text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-white/5 hover:bg-white/10 text-slate-300 transition disabled:opacity-40"
                        >
                          Edit
                        </button>
                        <button
                          onClick={() => testAccount(a.id)}
                          disabled={accountBusy}
                          className="text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-white/5 hover:bg-white/10 text-slate-300 transition disabled:opacity-40"
                        >
                          Test
                        </button>
                        <button
                          onClick={() => removeAccount(a.id)}
                          disabled={accountBusy}
                          className="text-xs px-3 py-1.5 rounded-lg border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 text-red-300 transition disabled:opacity-40"
                        >
                          Remove
                        </button>
                      </td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Per-lead send history */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
            Send history {sendsFilter ? `— ${sendsFilter}` : ""}
          </h2>
          {sendsFilter && (
            <button
              onClick={() => filterSends(undefined)}
              className="text-xs text-indigo-400 hover:text-indigo-300"
            >
              Clear filter
            </button>
          )}
        </div>
        {sendsLoading ? (
          <div className="h-32 rounded-2xl bg-white/5 animate-pulse" />
        ) : sends.length === 0 ? (
          <div className="rounded-2xl border border-white/8 bg-white/3 px-6 py-10 text-center text-slate-500">
            No sends {sendsFilter ? `with status "${sendsFilter}"` : "yet"} — nothing has gone through the queue.
          </div>
        ) : (
          <div className="rounded-2xl border border-white/8 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/8 bg-white/3">
                  {["Lead", "Step", "Status", "When", "Error"].map((h) => (
                    <th key={h} className="text-left px-5 py-3 text-slate-400 font-medium whitespace-nowrap">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {sends.map((s) => (
                  <tr key={s.id} className="hover:bg-white/3 transition-colors">
                    <td className="px-5 py-3 text-white font-medium whitespace-nowrap">
                      {s.business_name || s.email || `lead #${s.lead_id}`}
                      {s.business_name && s.email && (
                        <span className="block text-xs text-slate-500 font-normal">{s.email}</span>
                      )}
                    </td>
                    <td className="px-5 py-3 text-slate-300">{s.step_number}</td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${SEND_STATUS_COLORS[s.status] ?? "bg-slate-700 text-slate-300"}`}>
                        {s.status}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-slate-400 whitespace-nowrap">
                      {new Date(s.sent_at || s.scheduled_at).toLocaleString()}
                    </td>
                    <td className="px-5 py-3 text-red-400 text-xs max-w-xs truncate" title={s.error ?? ""}>
                      {s.error ?? "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <Pagination page={sendsPage} pageSize={SENDS_PAGE_SIZE} total={sendsTotal} onPageChange={setSendsPage} />
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
                  {["Name", "Status", "Leads", "Sent", "Pending", "Actions"].map((h) => (
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
                    <td className="px-5 py-3">
                      <button
                        onClick={() => toggleCampaignStatus(c)}
                        className={`text-xs px-3 py-1.5 rounded-lg border transition ${
                          c.status === "active"
                            ? "border-white/10 bg-white/5 hover:bg-white/10 text-slate-300"
                            : "border-green-500/30 bg-green-500/10 hover:bg-green-500/20 text-green-300"
                        }`}
                      >
                        {c.status === "active" ? "Pause" : "Resume"}
                      </button>
                    </td>
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
