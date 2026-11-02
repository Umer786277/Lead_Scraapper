"use client";

import {
  api,
  type CampaignScriptSet,
  type CampaignScriptSetInput,
  type EmailAccount,
  type EmailSequence,
  type EmailSequenceInput,
  type EmailTemplate,
  type ScriptSelectionMode,
} from "@/lib/api";
import { Mail } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import Pagination from "@/components/Pagination";

const SENDS_PAGE_SIZE = 25;
type SequenceStepDraft = { template_id: number; delay_days: number };

type SendRow = {
  id: number;
  status: string;
  step_number: number | null;
  scheduled_at: string;
  sent_at: string | null;
  error: string | null;
  lead_id: number;
  business_name: string | null;
  email: string | null;
  campaign_name: string;
  source: "campaign" | "send_now";
  template_name: string | null;
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
  sending: "bg-blue-900/50 text-blue-300",
  sent:    "bg-green-900/50 text-green-300",
  failed:  "bg-red-900/30 text-red-400",
  skipped: "bg-yellow-900/40 text-yellow-300",
};

export default function OutreachPage() {
  const [campaigns, setCampaigns] = useState<Record<string, unknown>[]>([]);
  const [queue, setQueue] = useState<Record<string, number>>({});
  const [templates, setTemplates] = useState<EmailTemplate[]>([]);
  const [sequences, setSequences] = useState<EmailSequence[]>([]);
  const [scriptSets, setScriptSets] = useState<CampaignScriptSet[]>([]);
  const [scriptSelectionMode, setScriptSelectionMode] =
    useState<ScriptSelectionMode>("sequential");
  const [scriptModeBusy, setScriptModeBusy] = useState(false);
  const [scriptModeMsg, setScriptModeMsg] = useState("");
  const [showScriptSetForm, setShowScriptSetForm] = useState(false);
  const [editingScriptSetId, setEditingScriptSetId] = useState<number | null>(null);
  const [scriptSetBusy, setScriptSetBusy] = useState(false);
  const [scriptSetMsg, setScriptSetMsg] = useState("");
  const [scriptSetForm, setScriptSetForm] = useState<CampaignScriptSetInput>({
    name: "",
    scripts: [{ name: "Script 1", steps: [{ subject: "", body: "" }] }],
  });
  const [showSequenceForm, setShowSequenceForm] = useState(false);
  const [editingSequenceId, setEditingSequenceId] = useState<number | null>(null);
  const [sequenceBusy, setSequenceBusy] = useState(false);
  const [sequenceMsg, setSequenceMsg] = useState("");
  const [sequenceForm, setSequenceForm] = useState({
    name: "",
    steps: [{ template_id: 0, delay_days: 0 }] as SequenceStepDraft[],
  });
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
  const [testEmailAccountId, setTestEmailAccountId] = useState<number | null>(null);
  const [testEmailTo, setTestEmailTo] = useState("");
  const [testEmailTemplateId, setTestEmailTemplateId] = useState<number | "">("");
  const [testEmailBusy, setTestEmailBusy] = useState(false);
  const [testEmailMsg, setTestEmailMsg] = useState("");
  const [showTemplateForm, setShowTemplateForm] = useState(false);
  const [editingTemplateId, setEditingTemplateId] = useState<number | null>(null);
  const [templateBusy, setTemplateBusy] = useState(false);
  const [templateMsg, setTemplateMsg] = useState("");
  const [templateForm, setTemplateForm] = useState({
    step_number: null as number | null,
    name: "",
    subject: "",
    body: "",
  });
  const [accountForm, setAccountForm] = useState({
    label: "", from_name: "", from_email: "", reply_to: "",
    smtp_host: "", smtp_port: 587, smtp_user: "", smtp_password: "", smtp_use_tls: true,
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
    Promise.all([
      api.outreach.campaigns(),
      api.outreach.queue(),
      api.outreach.templates(),
      api.outreach.limits(),
      api.outreach.sequences(),
      api.outreach.scriptSets(),
      api.outreach.scriptSelectionSettings(),
    ])
      .then(([c, q, t, l, savedSequences, savedScriptSets, scriptSettings]) => {
        setCampaigns(c as typeof campaigns);
        setQueue(q as typeof queue);
        setTemplates(t as typeof templates);
        setSequences(savedSequences);
        setScriptSets(savedScriptSets);
        setScriptSelectionMode(scriptSettings.mode);
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
        daily_limit: Number(accountForm.daily_limit),
        hourly_limit: Number(accountForm.hourly_limit),
        warmup_days: Number(accountForm.warmup_days),
        ...(accountForm.smtp_password ? {} : { smtp_password: undefined }),
      };
      if (editingAccountId) {
        await api.outreach.updateAccount(editingAccountId, payload);
      } else {
        await api.outreach.createAccount(payload);
      }
      setAccountMsg(editingAccountId ? "Account updated." : "Account added.");
      setShowAccountForm(false);
      setEditingAccountId(null);
      setAccountForm((f) => ({ ...f, from_email: "", smtp_password: "" }));
      loadAccounts();
    } catch (e) {
      setAccountMsg((e as Error).message);
    } finally {
      setAccountBusy(false);
    }
  }

  async function saveScriptSelectionMode(mode: ScriptSelectionMode) {
    setScriptModeBusy(true);
    setScriptModeMsg("");
    try {
      const saved = await api.outreach.updateScriptSelectionSettings(mode);
      setScriptSelectionMode(saved.mode);
      setScriptModeMsg(
        "Organization-wide mode saved. Unassigned leads in existing campaigns will use it.",
      );
    } catch (e) {
      setScriptModeMsg((e as Error).message);
    } finally {
      setScriptModeBusy(false);
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
        `SMTP ${res.smtp_ok ? "OK" : "FAILED"}` +
        (res.errors.length ? ` — ${res.errors.join("; ")}` : "")
      );
    } catch (e) {
      setAccountMsg((e as Error).message);
    } finally {
      setAccountBusy(false);
    }
  }

  function openTestEmail(account: EmailAccount) {
    setTestEmailAccountId(account.id);
    setTestEmailTo(account.from_email);
    setTestEmailTemplateId(templates[0]?.id ?? "");
    setTestEmailMsg("");
  }

  async function sendTestEmail() {
    if (testEmailAccountId === null || testEmailTemplateId === "") return;
    setTestEmailBusy(true);
    setTestEmailMsg("");
    try {
      await api.outreach.sendTestEmail(testEmailAccountId, {
        to_email: testEmailTo.trim(),
        template_id: testEmailTemplateId,
      });
      setTestEmailMsg(`Test email accepted by SMTP for ${testEmailTo.trim()}.`);
    } catch (e) {
      setTestEmailMsg((e as Error).message);
    } finally {
      setTestEmailBusy(false);
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

  function startNewTemplate() {
    setTemplateForm({ step_number: null, name: "", subject: "", body: "" });
    setEditingTemplateId(null);
    setTemplateMsg("");
    setShowTemplateForm(true);
  }

  function editTemplate(template: EmailTemplate) {
    setTemplateForm({
      step_number: template.step_number,
      name: template.name,
      subject: template.subject,
      body: template.body,
    });
    setEditingTemplateId(template.id);
    setTemplateMsg("");
    setShowTemplateForm(true);
  }

  async function saveTemplate() {
    setTemplateBusy(true);
    setTemplateMsg("");
    const payload = {
      step_number: templateForm.step_number,
      name: templateForm.name.trim(),
      subject: templateForm.subject.trim(),
      body: templateForm.body.trim(),
    };
    try {
      if (editingTemplateId !== null) {
        await api.outreach.updateTemplate(editingTemplateId, payload);
      } else {
        await api.outreach.createTemplate(payload);
      }
      const updatedTemplates = await api.outreach.templates();
      setTemplates(updatedTemplates);
      setTemplateMsg(editingTemplateId !== null ? "Template updated." : "Template added.");
      setShowTemplateForm(false);
      setEditingTemplateId(null);
      setTemplateForm((form) => ({ ...form, name: "", subject: "", body: "" }));
    } catch (e) {
      setTemplateMsg((e as Error).message);
    } finally {
      setTemplateBusy(false);
    }
  }

  function editSequence(sequence: EmailSequence) {
    setEditingSequenceId(sequence.id);
    setSequenceForm({
      name: sequence.name,
      steps: sequence.steps.map(({ template_id, delay_days }) => ({
        template_id,
        delay_days,
      })),
    });
    setSequenceMsg("");
    setShowSequenceForm(true);
  }

  async function saveSequence() {
    const payload: EmailSequenceInput = {
      name: sequenceForm.name.trim(),
      steps: sequenceForm.steps,
    };
    if (!payload.name || payload.steps.some((step) => !step.template_id)) {
      setSequenceMsg("Enter a sequence name and choose a script for every step.");
      return;
    }
    setSequenceBusy(true);
    setSequenceMsg("");
    try {
      if (editingSequenceId === null) {
        await api.outreach.createSequence(payload);
      } else {
        await api.outreach.updateSequence(editingSequenceId, payload);
      }
      setSequences(await api.outreach.sequences());
      setSequenceMsg(editingSequenceId === null ? "Sequence saved." : "Sequence updated.");
      setShowSequenceForm(false);
      setEditingSequenceId(null);
    } catch (e) {
      setSequenceMsg((e as Error).message);
    } finally {
      setSequenceBusy(false);
    }
  }

  async function removeSequence(sequence: EmailSequence) {
    if (!window.confirm(`Delete "${sequence.name}"? Existing campaigns keep their scheduled email snapshots.`)) {
      return;
    }
    setSequenceBusy(true);
    setSequenceMsg("");
    try {
      await api.outreach.deleteSequence(sequence.id);
      setSequences(await api.outreach.sequences());
      setSequenceMsg("Sequence deleted. Existing campaign steps were not changed.");
    } catch (e) {
      setSequenceMsg((e as Error).message);
    } finally {
      setSequenceBusy(false);
    }
  }

  function startNewScriptSet() {
    setEditingScriptSetId(null);
    setScriptSetForm({
      name: "",
      scripts: [{ name: "Script 1", steps: [{ subject: "", body: "" }] }],
    });
    setScriptSetMsg("");
    setShowScriptSetForm(true);
  }

  function editScriptSet(scriptSet: CampaignScriptSet) {
    setEditingScriptSetId(scriptSet.id);
    setScriptSetForm({
      name: scriptSet.name,
      scripts: scriptSet.scripts.map((script) => ({
        name: script.name,
        steps: script.steps.map((step) => ({ ...step })),
      })),
    });
    setScriptSetMsg("");
    setShowScriptSetForm(true);
  }

  async function saveScriptSet() {
    const scripts = scriptSetForm.scripts.map((script) => ({
      name: script.name.trim(),
      steps: script.steps.map((step) => ({
        subject: step.subject.trim(),
        body: step.body.trim(),
      })),
    }));
    if (
      !scriptSetForm.name.trim() ||
      scripts.some((script) => (
        !script.name ||
        !script.steps.length ||
        script.steps.some((step) => !step.subject || !step.body)
      ))
    ) {
      setScriptSetMsg("Enter a name and a subject and body for every script step.");
      return;
    }
    if (new Set(scripts.map((script) => script.steps.length)).size !== 1) {
      setScriptSetMsg(
        "All variants must have the same number of steps so campaign follow-up timing stays unchanged.",
      );
      return;
    }

    setScriptSetBusy(true);
    setScriptSetMsg("");
    try {
      const payload: CampaignScriptSetInput = {
        name: scriptSetForm.name.trim(),
        scripts,
      };
      if (editingScriptSetId === null) {
        await api.outreach.createScriptSet(payload);
      } else {
        await api.outreach.updateScriptSet(editingScriptSetId, payload);
      }
      setScriptSets(await api.outreach.scriptSets());
      await reloadOverview();
      setScriptSetMsg(
        editingScriptSetId === null
          ? "Script set saved."
          : "Script set updated. Assigned leads keep their original scripts.",
      );
      setShowScriptSetForm(false);
      setEditingScriptSetId(null);
    } catch (e) {
      setScriptSetMsg((e as Error).message);
    } finally {
      setScriptSetBusy(false);
    }
  }

  async function removeScriptSet(scriptSet: CampaignScriptSet) {
    if (!window.confirm(
      `Delete "${scriptSet.name}"? Campaigns already using it keep their copies.`,
    )) {
      return;
    }
    setScriptSetBusy(true);
    setScriptSetMsg("");
    try {
      await api.outreach.deleteScriptSet(scriptSet.id);
      setScriptSets(await api.outreach.scriptSets());
      setScriptSetMsg("Script set deleted. Existing campaigns keep their copies.");
    } catch (e) {
      setScriptSetMsg((e as Error).message);
    } finally {
      setScriptSetBusy(false);
    }
  }

  function moveScriptVariant(index: number, direction: -1 | 1) {
    setScriptSetForm((form) => {
      const destination = index + direction;
      if (destination < 0 || destination >= form.scripts.length) return form;
      const scripts = [...form.scripts];
      [scripts[index], scripts[destination]] = [
        scripts[destination],
        scripts[index],
      ];
      return { ...form, scripts };
    });
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
  const editingAssignedTemplate = templates.some(
    (template) => template.id === editingTemplateId && template.step_number !== null,
  );

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
                      ? ["Sender", "SMTP", "Limits", "Status", "Actions"]
                      : ["Sender", "SMTP", "Limits", "Status"]
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
                          onClick={() => openTestEmail(a)}
                          disabled={accountBusy || a.status !== "active"}
                          className="text-xs px-3 py-1.5 rounded-lg border border-indigo-500/30 bg-indigo-500/10 hover:bg-indigo-500/20 text-indigo-300 transition disabled:opacity-40"
                        >
                          Send test
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
        {testEmailAccountId !== null && (
          <div className="mt-3 rounded-2xl border border-indigo-500/20 bg-indigo-500/5 p-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-white">Send a template test</h3>
              <button
                onClick={() => setTestEmailAccountId(null)}
                disabled={testEmailBusy}
                className="text-xs text-slate-400 hover:text-white disabled:opacity-40"
              >
                Cancel
              </button>
            </div>
            <p className="text-xs text-slate-400">
              Sends one real email to the test address using this sender. It won’t be sent to a lead or added to campaign history.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <label className="text-xs text-slate-400">
                Test recipient
                <input
                  type="email"
                  required
                  value={testEmailTo}
                  onChange={(e) => setTestEmailTo(e.target.value)}
                  placeholder="you@company.com"
                  className={`mt-1 ${inputCls}`}
                />
              </label>
              <label className="text-xs text-slate-400">
                Template
                <select
                  value={testEmailTemplateId}
                  onChange={(e) => setTestEmailTemplateId(e.target.value ? Number(e.target.value) : "")}
                  className={`mt-1 ${inputCls} [&>option]:bg-slate-900 [&>option]:text-white`}
                >
                  <option value="">Select a template</option>
                  {templates.map((template) => (
                    <option key={template.id} value={template.id}>{template.name}</option>
                  ))}
                </select>
              </label>
            </div>
            {testEmailMsg && (
              <p className="text-xs text-slate-300">{testEmailMsg}</p>
            )}
            <button
              onClick={sendTestEmail}
              disabled={
                testEmailBusy ||
                !testEmailTo.trim() ||
                testEmailTemplateId === "" ||
                templates.length === 0
              }
              className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-semibold transition"
            >
              {testEmailBusy ? "Sending…" : "Send test email"}
            </button>
            {templates.length === 0 && (
              <p className="text-xs text-amber-300">Create an email template before sending a test.</p>
            )}
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
                    <td className="px-5 py-3 text-slate-300">
                      {s.source === "send_now"
                        ? s.template_name || "Manual send"
                        : `Step ${s.step_number}${s.template_name ? ` · ${s.template_name}` : ""}`}
                    </td>
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
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
            Email templates ({templates.length})
          </h2>
          {canManageAccounts && (
            <button
              onClick={startNewTemplate}
              className="text-xs px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition"
            >
              Add script
            </button>
          )}
        </div>
        {templateMsg && (
          <div className="mb-3 rounded-lg bg-white/5 border border-white/10 px-4 py-2 text-slate-300 text-xs">
            {templateMsg}
          </div>
        )}
        {showTemplateForm && (
          <div className="mb-4 rounded-2xl border border-white/8 bg-white/3 p-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-white">
                {editingTemplateId !== null ? "Edit email template" : "Add email template"}
              </h3>
              <button
                onClick={() => {
                  setShowTemplateForm(false);
                  setEditingTemplateId(null);
                }}
                disabled={templateBusy}
                className="text-xs text-slate-400 hover:text-white disabled:opacity-40"
              >
                Cancel
              </button>
            </div>
            <p className="text-xs text-slate-400">
              Choose a sequence step from 1–4. Changing an assigned step swaps it with the template in that position. Personalize with {"{{business_name}}"}, {"{{first_name}}"}, {"{{hook}}"}, and {"{{demo_clip}}"}.
            </p>
            <label className="block text-xs text-slate-400">
              Automatic sequence step (optional)
              <select
                value={templateForm.step_number ?? ""}
                onChange={(e) => setTemplateForm((form) => ({
                  ...form,
                  step_number: e.target.value ? Number(e.target.value) : null,
                }))}
                className={`mt-1 ${inputCls} [&>option]:bg-slate-900 [&>option]:text-white`}
              >
                {!editingAssignedTemplate && (
                  <option value="">Additional script (choose in a custom campaign)</option>
                )}
                {[1, 2, 3, 4].map((step) => (
                  <option
                    key={step}
                    value={step}
                    disabled={
                      !editingAssignedTemplate &&
                      templates.some((template) => template.step_number === step)
                    }
                  >
                    Step {step}
                  </option>
                ))}
              </select>
            </label>
            <label className="block text-xs text-slate-400">
              Template name
              <input
                type="text"
                maxLength={120}
                value={templateForm.name}
                onChange={(e) => setTemplateForm((form) => ({ ...form, name: e.target.value }))}
                className={`mt-1 ${inputCls}`}
              />
            </label>
            <label className="block text-xs text-slate-400">
              Email subject
              <input
                type="text"
                maxLength={998}
                value={templateForm.subject}
                onChange={(e) => setTemplateForm((form) => ({ ...form, subject: e.target.value }))}
                className={`mt-1 ${inputCls}`}
              />
            </label>
            <label className="block text-xs text-slate-400">
              Email body
              <textarea
                rows={10}
                maxLength={20000}
                value={templateForm.body}
                onChange={(e) => setTemplateForm((form) => ({ ...form, body: e.target.value }))}
                className={`mt-1 ${inputCls} resize-y`}
              />
            </label>
            <button
              onClick={saveTemplate}
              disabled={
                templateBusy ||
                !templateForm.name.trim() ||
                !templateForm.subject.trim() ||
                !templateForm.body.trim()
              }
              className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-semibold transition"
            >
              {templateBusy ? "Saving…" : editingTemplateId !== null ? "Save changes" : "Save template"}
            </button>
          </div>
        )}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {templates.map((t) => (
            <div key={t.id} className="rounded-xl border border-white/8 bg-white/3 px-5 py-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-white mb-1">
                    {t.step_number === null ? "Additional script" : `Step ${t.step_number}`} — {t.name}
                  </p>
                  <p className="text-xs text-slate-400 truncate">{t.subject}</p>
                </div>
                {canManageAccounts && (
                  <button
                    onClick={() => editTemplate(t)}
                    disabled={templateBusy}
                    className="text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-white/5 hover:bg-white/10 text-slate-300 transition disabled:opacity-40"
                  >
                    Edit
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Campaign script sets */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
              Campaign script sets ({scriptSets.length})
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Selection mode applies to every script set in this organization. Each variant contains the full campaign copy; campaign timing comes from the selected sequence.
            </p>
          </div>
          {canManageAccounts && (
            <button
              onClick={startNewScriptSet}
              disabled={scriptSetBusy}
              className="text-xs px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white font-medium transition"
            >
              Create script set
            </button>
          )}
        </div>
        <div className="mb-3 flex flex-wrap items-end gap-3 rounded-xl border border-white/8 bg-white/3 p-3">
          <label className="min-w-56 text-xs text-slate-400">
            Organization-wide script selection mode
            <select
              value={scriptSelectionMode}
              onChange={(e) => saveScriptSelectionMode(
                e.target.value as ScriptSelectionMode,
              )}
              disabled={!canManageAccounts || scriptModeBusy}
              className={`mt-1 ${inputCls} disabled:opacity-50 [&>option]:bg-slate-900 [&>option]:text-white`}
            >
              <option value="sequential">Sequential</option>
              <option value="random">Random</option>
            </select>
          </label>
          <span className="pb-2 text-xs text-slate-500">
            Applies to all script sets and unassigned campaign leads. Random uses every variant once in a shuffled cycle before repeating.
          </span>
          {scriptModeMsg && (
            <span className="pb-2 text-xs text-slate-400">{scriptModeMsg}</span>
          )}
        </div>
        <p className="mb-3 text-xs text-slate-500">
          When a set is edited, campaigns using it update for unassigned leads; assigned leads keep their saved copy for retries and every follow-up.
        </p>
        {scriptSetMsg && (
          <div className="mb-3 rounded-lg bg-white/5 border border-white/10 px-4 py-2 text-slate-300 text-xs">
            {scriptSetMsg}
          </div>
        )}
        {showScriptSetForm && (
          <div className="mb-4 rounded-2xl border border-white/8 bg-white/3 p-4 space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-white">
                {editingScriptSetId === null ? "Create campaign script set" : "Edit campaign script set"}
              </h3>
              <button
                onClick={() => setShowScriptSetForm(false)}
                disabled={scriptSetBusy}
                className="text-xs text-slate-400 hover:text-white disabled:opacity-40"
              >
                Cancel
              </button>
            </div>
            <p className="text-xs text-slate-400">
              Add 1–5 variants. Each variant contains 1–4 step-specific subject/body pairs; every variant must have the same number of steps. Merge tags are rendered when each email is sent.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              <label className="text-xs text-slate-400">
                Set name
                <input
                  type="text"
                  maxLength={120}
                  value={scriptSetForm.name}
                  onChange={(e) => setScriptSetForm((form) => ({
                    ...form,
                    name: e.target.value,
                  }))}
                  className={`mt-1 ${inputCls}`}
                />
              </label>
            </div>
            <div className="space-y-3">
              {scriptSetForm.scripts.map((script, scriptIndex) => (
                <div
                  key={scriptIndex}
                  className="rounded-xl border border-white/8 bg-black/10 p-4 space-y-3"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-xs text-slate-500 font-mono">
                      Variant {scriptIndex + 1}
                    </span>
                    <input
                      type="text"
                      maxLength={120}
                      value={script.name}
                      aria-label={`Variant ${scriptIndex + 1} name`}
                      onChange={(e) => setScriptSetForm((form) => ({
                        ...form,
                        scripts: form.scripts.map((item, index) => (
                          index === scriptIndex ? { ...item, name: e.target.value } : item
                        )),
                      }))}
                      className={`min-w-48 flex-1 ${inputCls}`}
                    />
                    <button
                      onClick={() => moveScriptVariant(scriptIndex, -1)}
                      disabled={scriptSetBusy || scriptIndex === 0}
                      className="rounded border border-white/10 px-2 py-1 text-xs text-slate-300 disabled:opacity-30"
                      aria-label={`Move variant ${scriptIndex + 1} up`}
                    >
                      ↑
                    </button>
                    <button
                      onClick={() => moveScriptVariant(scriptIndex, 1)}
                      disabled={scriptSetBusy || scriptIndex === scriptSetForm.scripts.length - 1}
                      className="rounded border border-white/10 px-2 py-1 text-xs text-slate-300 disabled:opacity-30"
                      aria-label={`Move variant ${scriptIndex + 1} down`}
                    >
                      ↓
                    </button>
                    <button
                      onClick={() => setScriptSetForm((form) => ({
                        ...form,
                        scripts: form.scripts.filter((_, index) => index !== scriptIndex),
                      }))}
                      disabled={scriptSetBusy || scriptSetForm.scripts.length <= 1}
                      className="rounded border border-red-500/20 px-2 py-1 text-xs text-red-300 disabled:opacity-30"
                    >
                      Remove
                    </button>
                  </div>
                  {script.steps.map((step, stepIndex) => (
                    <div
                      key={stepIndex}
                      className="grid grid-cols-1 md:grid-cols-2 gap-3 rounded-lg border border-white/5 p-3"
                    >
                      <div className="md:col-span-2 flex items-center justify-between">
                        <span className="text-xs text-slate-400">
                          {stepIndex === 0 ? "Initial email" : `Follow-up ${stepIndex}`}
                        </span>
                        <button
                          onClick={() => setScriptSetForm((form) => ({
                            ...form,
                            scripts: form.scripts.map((item, index) => (
                              index === scriptIndex
                                ? { ...item, steps: item.steps.filter((_, i) => i !== stepIndex) }
                                : item
                            )),
                          }))}
                          disabled={scriptSetBusy || script.steps.length <= 1}
                          className="text-xs text-red-300 disabled:opacity-30"
                        >
                          Remove step
                        </button>
                      </div>
                      <label className="text-xs text-slate-400">
                        Subject
                        <input
                          type="text"
                          maxLength={998}
                          value={step.subject}
                          onChange={(e) => setScriptSetForm((form) => ({
                            ...form,
                            scripts: form.scripts.map((item, index) => (
                              index === scriptIndex
                                ? {
                                  ...item,
                                  steps: item.steps.map((content, i) => (
                                    i === stepIndex
                                      ? { ...content, subject: e.target.value }
                                      : content
                                  )),
                                }
                                : item
                            )),
                          }))}
                          className={`mt-1 ${inputCls}`}
                        />
                      </label>
                      <label className="text-xs text-slate-400 md:col-span-2">
                        Body
                        <textarea
                          rows={5}
                          maxLength={20000}
                          value={step.body}
                          onChange={(e) => setScriptSetForm((form) => ({
                            ...form,
                            scripts: form.scripts.map((item, index) => (
                              index === scriptIndex
                                ? {
                                  ...item,
                                  steps: item.steps.map((content, i) => (
                                    i === stepIndex
                                      ? { ...content, body: e.target.value }
                                      : content
                                  )),
                                }
                                : item
                            )),
                          }))}
                          className={`mt-1 ${inputCls} resize-y`}
                        />
                      </label>
                    </div>
                  ))}
                  <button
                    onClick={() => setScriptSetForm((form) => ({
                      ...form,
                      scripts: form.scripts.map((item, index) => (
                        index === scriptIndex
                          ? {
                            ...item,
                            steps: [...item.steps, { subject: "", body: "" }],
                          }
                          : item
                      )),
                    }))}
                    disabled={scriptSetBusy || script.steps.length >= 4}
                    className="text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-30"
                  >
                    + Add follow-up step
                  </button>
                </div>
              ))}
              <button
                onClick={() => setScriptSetForm((form) => {
                  const stepCount = form.scripts[0]?.steps.length ?? 1;
                  return {
                    ...form,
                    scripts: [
                      ...form.scripts,
                      {
                        name: `Script ${form.scripts.length + 1}`,
                        steps: Array.from(
                          { length: stepCount },
                          () => ({ subject: "", body: "" }),
                        ),
                      },
                    ],
                  };
                })}
                disabled={scriptSetBusy || scriptSetForm.scripts.length >= 5}
                className="text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-30"
              >
                + Add variant
              </button>
            </div>
            <button
              onClick={saveScriptSet}
              disabled={scriptSetBusy || !scriptSetForm.name.trim()}
              className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-semibold transition"
            >
              {scriptSetBusy ? "Saving…" : editingScriptSetId === null ? "Save script set" : "Save changes"}
            </button>
          </div>
        )}
        {scriptSets.length === 0 ? (
          <div className="rounded-xl border border-white/8 bg-white/3 px-5 py-5 text-sm text-slate-500">
            No campaign script sets yet. Create one to assign full sequence variants to campaign leads.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {scriptSets.map((scriptSet) => (
              <div key={scriptSet.id} className="rounded-xl border border-white/8 bg-white/3 px-5 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium text-white">{scriptSet.name}</p>
                    <p className="mt-1 text-xs text-slate-500">
                      {scriptSet.scripts.length} variants · {scriptSet.scripts[0]?.steps.length ?? 0} steps
                    </p>
                    <ol className="mt-3 space-y-2">
                      {scriptSet.scripts.map((script, index) => (
                        <li key={`${scriptSet.id}-${index}`} className="text-xs text-slate-400">
                          <span className="text-slate-300">{index + 1}. {script.name}</span>
                          {script.steps.map((step, stepIndex) => (
                            <span key={stepIndex} className="block truncate pl-4 text-slate-500">
                              {stepIndex === 0 ? "Initial" : `Follow-up ${stepIndex}`} · {step.subject}
                            </span>
                          ))}
                        </li>
                      ))}
                    </ol>
                  </div>
                  {canManageAccounts && (
                    <div className="flex gap-2">
                      <button
                        onClick={() => editScriptSet(scriptSet)}
                        disabled={scriptSetBusy}
                        className="text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-white/5 hover:bg-white/10 text-slate-300 transition disabled:opacity-40"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => removeScriptSet(scriptSet)}
                        disabled={scriptSetBusy}
                        className="text-xs px-3 py-1.5 rounded-lg border border-red-500/20 bg-red-500/5 hover:bg-red-500/10 text-red-300 transition disabled:opacity-40"
                      >
                        Delete
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Reusable sequences */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider">
              Sequences ({sequences.length})
            </h2>
            <p className="mt-1 text-xs text-slate-500">
              Start with one email; add up to three optional follow-ups.
            </p>
          </div>
        </div>
        {sequenceMsg && (
          <div className="mb-3 rounded-lg bg-white/5 border border-white/10 px-4 py-2 text-slate-300 text-xs">
            {sequenceMsg}
          </div>
        )}
        {showSequenceForm && (
          <div className="mb-4 rounded-2xl border border-white/8 bg-white/3 p-4 space-y-3">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-semibold text-white">
                Edit sequence
              </h3>
              <button
                onClick={() => setShowSequenceForm(false)}
                disabled={sequenceBusy}
                className="text-xs text-slate-400 hover:text-white disabled:opacity-40"
              >
                Cancel
              </button>
            </div>
            <label className="block text-xs text-slate-400">
              Sequence name
              <input
                type="text"
                maxLength={120}
                value={sequenceForm.name}
                onChange={(e) => setSequenceForm((form) => ({ ...form, name: e.target.value }))}
                className={`mt-1 ${inputCls}`}
                placeholder="e.g. Initial email + follow-ups"
              />
            </label>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs text-slate-400 uppercase tracking-wider font-semibold">
                  Emails in order
                </p>
                <button
                  onClick={() => setSequenceForm((form) => ({
                    ...form,
                    steps: [...form.steps, { template_id: 0, delay_days: 3 }],
                  }))}
                  disabled={sequenceForm.steps.length >= 4 || sequenceBusy}
                  className="text-xs text-indigo-400 hover:text-indigo-300 disabled:opacity-40"
                >
                  + Add follow-up
                </button>
              </div>
              {sequenceForm.steps.map((step, index) => (
                <div key={index} className="flex items-center gap-2">
                  <span className="w-5 text-xs text-slate-500 font-mono">{index + 1}</span>
                  <select
                    value={step.template_id}
                    onChange={(e) => setSequenceForm((form) => ({
                      ...form,
                      steps: form.steps.map((item, itemIndex) => itemIndex === index
                        ? { ...item, template_id: Number(e.target.value) }
                        : item),
                    }))}
                    className={`flex-1 ${inputCls} [&>option]:bg-slate-900 [&>option]:text-white`}
                  >
                    <option value={0}>Choose an email script</option>
                    {templates.map((template) => (
                      <option key={template.id} value={template.id}>{template.name}</option>
                    ))}
                  </select>
                  {index === 0 ? (
                    <span className="text-xs text-slate-500 whitespace-nowrap">Initial email</span>
                  ) : (
                    <label className="flex items-center gap-1.5 text-xs text-slate-400 whitespace-nowrap">
                      <input
                        type="number"
                        min={0}
                        max={365}
                        value={step.delay_days}
                        onChange={(e) => setSequenceForm((form) => ({
                          ...form,
                          steps: form.steps.map((item, itemIndex) => itemIndex === index
                            ? { ...item, delay_days: Math.min(365, Math.max(0, Number(e.target.value) || 0)) }
                            : item),
                        }))}
                        className="w-16 bg-white/5 border border-white/10 rounded-lg px-2 py-2 text-sm text-white text-center focus:outline-none"
                      />
                      days after previous
                    </label>
                  )}
                  {index > 0 && (
                    <button
                      onClick={() => setSequenceForm((form) => ({
                        ...form,
                        steps: form.steps.filter((_, itemIndex) => itemIndex !== index),
                      }))}
                      disabled={sequenceBusy}
                      className="text-slate-500 hover:text-red-400 text-lg leading-none px-1"
                      title="Remove follow-up"
                    >
                      ×
                    </button>
                  )}
                </div>
              ))}
              <p className="text-xs text-slate-500">
                Follow-ups are sent only if no reply, unsubscribe, or bounce stops the sequence.
              </p>
            </div>
            <button
              onClick={saveSequence}
              disabled={
                sequenceBusy ||
                !sequenceForm.name.trim() ||
                sequenceForm.steps.some((step) => !step.template_id)
              }
              className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-semibold transition"
            >
              {sequenceBusy ? "Saving…" : "Save sequence"}
            </button>
          </div>
        )}
        {sequences.length === 0 ? (
          <div className="rounded-xl border border-white/8 bg-white/3 px-5 py-5 text-sm text-slate-500">
            No saved sequences yet. Campaigns require a saved sequence for step timing.
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {sequences.map((sequence) => (
              <div key={sequence.id} className="rounded-xl border border-white/8 bg-white/3 px-5 py-4">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="font-medium text-white mb-2">{sequence.name}</p>
                    <ol className="space-y-1">
                      {sequence.steps.map((step) => (
                        <li key={step.step_number} className="text-xs text-slate-400">
                          {step.step_number === 1
                            ? `Initial email: ${step.template_name}`
                            : `Follow-up ${step.step_number - 1} · ${step.delay_days} days after previous: ${step.template_name}`}
                        </li>
                      ))}
                    </ol>
                  </div>
                  {canManageAccounts && (
                    <div className="flex gap-2">
                      <button
                        onClick={() => editSequence(sequence)}
                        disabled={sequenceBusy}
                        className="text-xs px-3 py-1.5 rounded-lg border border-white/10 bg-white/5 hover:bg-white/10 text-slate-300 transition"
                      >
                        Edit
                      </button>
                      <button
                        onClick={() => removeSequence(sequence)}
                        disabled={sequenceBusy}
                        className="text-xs px-3 py-1.5 rounded-lg border border-red-500/20 bg-red-500/5 hover:bg-red-500/10 text-red-300 transition"
                      >
                        Delete
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
