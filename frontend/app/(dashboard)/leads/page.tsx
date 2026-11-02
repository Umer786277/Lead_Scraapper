"use client";

import { api, type Lead, type LeadBucketCounts } from "@/lib/api";
import { isLeadStatus, LEAD_STATUSES } from "@/lib/lead-status";
import ConfirmDialog from "@/components/ConfirmDialog";
import { CheckCircle, Download, Layers, Mail, Phone, Search, Send, Star, Upload, Users, XCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { Suspense, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Pagination from "@/components/Pagination";

const PAGE_SIZE = 50;

type Bucket = "all" | "call" | "email" | "pending" | "none";
type Template = { id: number; name: string; subject: string };

const BUCKETS: { key: Bucket; label: string; icon: LucideIcon; color: string }[] = [
  { key: "call",    label: "Cold Calling",       icon: Phone,   color: "text-green-400  border-green-500/30  bg-green-500/5" },
  { key: "email",   label: "Email Outreach",      icon: Mail,   color: "text-indigo-400 border-indigo-500/30 bg-indigo-500/5" },
  { key: "pending", label: "Pending Extraction",  icon: Search, color: "text-yellow-400 border-yellow-500/30 bg-yellow-500/5" },
  { key: "none",    label: "No Contact",          icon: XCircle, color: "text-red-400    border-red-500/30    bg-red-500/5" },
];

const STATUS_COLORS: Record<string, string> = {
  new:        "bg-slate-700 text-slate-300",
  contacted:  "bg-blue-900/50 text-blue-300",
  snoozed:    "bg-amber-900/50 text-amber-300",
  replied:    "bg-green-900/50 text-green-300",
  converted:  "bg-purple-900/50 text-purple-300",
  dead:       "bg-red-900/30 text-red-400",
};

type BuilderStep = { template_id: number; delay_days: number };
type BulkConfirmation = {
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => Promise<void>;
};

export default function LeadsPage() {
  return (
    <Suspense fallback={<p className="text-sm text-slate-400">Loading leads...</p>}>
      <LeadsContent />
    </Suspense>
  );
}

function LeadsContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [bucket, setBucket] = useState<Bucket>("call");
  const [leads, setLeads] = useState<Lead[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [counts, setCounts] = useState<LeadBucketCounts | null>(null);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [importingCsv, setImportingCsv] = useState(false);
  const [importResult, setImportResult] = useState<{
    message: string;
    errors: string[];
  } | null>(null);
  const importFileRef = useRef<HTMLInputElement>(null);
  const requestedStatus = searchParams.get("status");
  const statusFilter =
    requestedStatus === "all" || isLeadStatus(requestedStatus)
      ? requestedStatus
      : "all";
  const [cityFilter, setCityFilter] = useState("all");
  const [countryFilter, setCountryFilter] = useState("all");
  const [countries, setCountries] = useState<string[]>([]);
  const [citiesForCountry, setCitiesForCountry] = useState<string[]>([]);
  const [noteModal, setNoteModal] = useState<{ name: string; note: string } | null>(null);
  const [bulkConfirmation, setBulkConfirmation] = useState<BulkConfirmation | null>(null);
  const [error, setError] = useState("");

  function changeStatusFilter(status: string) {
    const params = new URLSearchParams(searchParams.toString());
    if (status === "all") params.delete("status");
    else params.set("status", status);
    const query = params.toString();
    router.push(query ? `/leads?${query}` : "/leads");
    setPage(1);
  }

  // ── Selection + bulk actions ──────────────────────────────
  const [selected, setSelected] = useState<number[]>([]);
  const [bulkMsg, setBulkMsg] = useState<string | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [builderOpen, setBuilderOpen] = useState(false);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [builderName, setBuilderName] = useState("");
  const [builderSteps, setBuilderSteps] = useState<BuilderStep[]>([]);

  // Any filter change invalidates the current page — handlers reset to page 1.

  // Load geo dropdown options from the DB (distinct countries/cities).
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const c = await api.leads.countries();
        if (!active) return;
        setCountries(c.countries);
      } catch {
        /* non-fatal — dropdowns just stay empty */
      }
    })();
    return () => { active = false; };
  }, []);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const c = await api.leads.cities(countryFilter === "all" ? undefined : countryFilter);
        if (!active) return;
        setCitiesForCountry(c.cities);
      } catch {
        /* non-fatal */
      }
    })();
    return () => { active = false; };
  }, [countryFilter]);

  useEffect(() => {
    let active = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const params: Record<string, string> = {
          bucket,
          limit: String(PAGE_SIZE),
          offset: String((page - 1) * PAGE_SIZE),
        };
        if (search) params.q = search;
        if (statusFilter !== "all") params.status = statusFilter;
        if (countryFilter !== "all") params.country = countryFilter;
        if (cityFilter !== "all") params.city = cityFilter;
        const res = await api.leads.list(params);
        if (!active) return;
        setLeads(res.items);
        setTotal(res.total);
        setCounts(res.bucket_counts);
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [bucket, search, statusFilter, countryFilter, cityFilter, page]);

  async function updateStatus(id: number, status: string) {
    await api.leads.updateStatus(id, status);
    setLeads((prev) => prev.map((l) => (l.id === id ? { ...l, status } : l)));
  }

  function downloadCSV() {
    if (!leads.length) return;
    const keys = Object.keys(leads[0]) as (keyof Lead)[];
    const rows = [keys.join(","), ...leads.map((l) => keys.map((k) => JSON.stringify(l[k] ?? "")).join(","))];
    const blob = new Blob([rows.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `leads_${bucket}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function importCSV(file?: File) {
    if (!file) return;
    setImportingCsv(true);
    setImportResult(null);
    setError("");
    try {
      const result = await api.leads.importCsv(file);
      setImportResult({
        message: `Imported ${result.imported} lead(s); skipped ${result.skipped} duplicate(s); ${result.invalid} invalid row(s).`,
        errors: result.errors,
      });
      setBucket("all");
      setCountryFilter("all");
      setCityFilter("all");
      setPage(1);
      try {
        const countryOptions = await api.leads.countries();
        setCountries(countryOptions.countries);
      } catch {
        setImportResult((current) => current ? {
          ...current,
          errors: [...current.errors, "Imported successfully, but country filters could not be refreshed."],
        } : current);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setImportingCsv(false);
      if (importFileRef.current) importFileRef.current.value = "";
    }
  }

  // ── Selection helpers ─────────────────────────────────────
  function toggleLead(id: number) {
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]
    );
    setBulkMsg(null);
  }

  function togglePage() {
    const ids = leads.map((l) => l.id);
    const allSelected = ids.length > 0 && ids.every((id) => selected.includes(id));
    setSelected((prev) => (allSelected ? prev.filter((id) => !ids.includes(id)) : Array.from(new Set([...prev, ...ids]))));
  }

  async function openBuilder() {
    setBulkMsg(null);
    if (!templates.length) {
      try {
        const t = (await api.outreach.templates()) as Template[];
        setTemplates(t);
      } catch (e) {
        setError((e as Error).message);
        return;
      }
    }
    setBuilderName("");
    setBuilderSteps(defaultSteps());
    setBuilderOpen(true);
  }

  function defaultSteps(): BuilderStep[] {
    const t = templates;
    const delays = [0, 3, 7, 14];
    return t.slice(0, 4).map((tmpl, i) => ({
      template_id: tmpl.id,
      delay_days: delays[i] ?? i,
    }));
  }

  async function runBulk(fn: () => Promise<unknown>, success: string) {
    if (!selected.length) return;
    setBulkBusy(true);
    setBulkMsg(null);
    try {
      await fn();
      setBulkMsg(success);
      setSelected([]);
      await reloadLeads();
    } catch (e) {
      setBulkMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setBulkBusy(false);
    }
  }

  async function reloadLeads() {
    try {
      const params: Record<string, string> = {
        bucket,
        limit: String(PAGE_SIZE),
        offset: String((page - 1) * PAGE_SIZE),
      };
      if (search) params.q = search;
      if (statusFilter !== "all") params.status = statusFilter;
      if (countryFilter !== "all") params.country = countryFilter;
      if (cityFilter !== "all") params.city = cityFilter;
      const res = await api.leads.list(params);
      setLeads(res.items);
      setTotal(res.total);
      setCounts(res.bucket_counts);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  function emailNow() {
    const ids = selected;
    setBulkConfirmation({
      title: "Send intro emails now?",
      description: `Send the intro email to ${ids.length} selected lead${ids.length === 1 ? "" : "s"} immediately?`,
      confirmLabel: "Send emails",
      onConfirm: () => runBulk(
        () => api.outreach.sendNow({ lead_ids: ids }),
        `Queued immediate send for ${ids.length} lead(s).`
      ),
    });
  }

  function queueCalls() {
    const ids = selected;
    runBulk(
      async () => {
        const res = await api.calls.queue(ids);
        if (res.skipped > 0) throw new Error(`${res.skipped} had no phone number and were skipped`);
      },
      `Queued ${ids.length} lead(s) for outbound calling.`
    );
  }

  function excludeSelected() {
    const ids = selected;
    setBulkConfirmation({
      title: "Exclude selected leads?",
      description: `Exclude ${ids.length} selected lead${ids.length === 1 ? "" : "s"} from all outreach? Pending sends will be cancelled.`,
      confirmLabel: "Exclude leads",
      onConfirm: () => runBulk(
        () => api.leads.exclude(ids),
        `Excluded ${ids.length} lead(s) — pending sends cancelled.`
      ),
    });
  }

  function unexcludeSelected() {
    runBulk(
      () => api.leads.unexclude(selected),
      `Re-enabled ${selected.length} lead(s) for outreach.`
    );
  }

  async function createCampaign() {
    if (!builderName.trim()) {
      setBulkMsg("Give the campaign a name.");
      return;
    }
    const steps = builderSteps.filter((s) => s.template_id);
    if (!steps.length) {
      setBulkMsg("At least one step with a template is required.");
      return;
    }
    setBulkBusy(true);
    setBulkMsg(null);
    try {
      const res = await api.outreach.createCampaign({
        name: builderName.trim(),
        lead_ids: selected,
        steps,
      });
      setBulkMsg(`Campaign #${res.id} created with ${selected.length} lead(s).`);
      setBuilderOpen(false);
      setSelected([]);
      await reloadLeads();
    } catch (e) {
      setBulkMsg(`Failed: ${(e as Error).message}`);
    } finally {
      setBulkBusy(false);
    }
  }

  const activeBucket = BUCKETS.find((b) => b.key === bucket)!

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="flex items-center gap-2 text-2xl font-bold text-white">
          <Users className="text-indigo-400" size={24} /> Leads
        </h1>
        <p className="text-slate-400 text-sm mt-1">Segmented by contact readiness. Click a tab to filter.</p>
      </div>

      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-4 py-3 text-red-400 text-sm">{error}</div>
      )}

      {/* Bucket tabs */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {BUCKETS.map(({ key, label, icon: Icon, color }) => {
          const count = counts ? counts[key] : "—";
          const active = bucket === key;
          return (
            <button
              key={key}
              onClick={() => { setBucket(key); setPage(1); }}
              className={`rounded-xl border px-4 py-3 text-left transition-all ${color} ${
                active ? "ring-2 ring-indigo-400/40" : "opacity-60 hover:opacity-100"
              }`}
            >
              <Icon size={20} className="mb-1" />
              <div className="font-bold text-white text-xl">{count}</div>
              <div className="text-xs mt-0.5 text-slate-400">{label}</div>
            </button>
          );
        })}
      </div>

      {/* Filters row */}
      <div className="flex flex-wrap gap-3 items-center">
        <input
          type="text"
          placeholder="Search name, city, domain…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
          className="bg-white/5 border border-white/10 rounded-lg px-4 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40 w-64"
        />
        <select
          value={statusFilter}
          onChange={(e) => changeStatusFilter(e.target.value)}
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
        >
          {["all", ...LEAD_STATUSES].map((s) => (
            <option key={s} value={s} className="bg-[#1a1d2e]">{s === "all" ? "All statuses" : s}</option>
          ))}
        </select>
        <select
          value={countryFilter}
          onChange={(e) => {
            setCountryFilter(e.target.value);
            setCityFilter("all");
            setPage(1);
          }}
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
        >
          <option value="all" className="bg-[#1a1d2e]">All countries</option>
          {countries.map((c) => (
            <option key={c} value={c} className="bg-[#1a1d2e]">{c}</option>
          ))}
        </select>
        <select
          value={cityFilter}
          onChange={(e) => { setCityFilter(e.target.value); setPage(1); }}
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
        >
          <option value="all" className="bg-[#1a1d2e]">All cities</option>
          {citiesForCountry.map((c) => (
            <option key={c} value={c} className="bg-[#1a1d2e]">{c}</option>
          ))}
        </select>
        <div className="flex-1" />
        <input
          ref={importFileRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          onChange={(event) => void importCSV(event.target.files?.[0])}
        />
        <button
          onClick={() => importFileRef.current?.click()}
          disabled={importingCsv}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-indigo-600 hover:bg-indigo-500 border border-indigo-500/40 rounded-lg text-white transition disabled:opacity-40"
        >
          <Upload size={14} /> {importingCsv ? "Importing…" : "Import CSV"}
        </button>
        <button
          onClick={downloadCSV}
          disabled={!leads.length}
          className="flex items-center gap-2 px-4 py-2 text-sm bg-white/5 hover:bg-white/10 border border-white/10 rounded-lg text-slate-300 transition disabled:opacity-40"
        >
          <Download size={14} /> Export CSV
        </button>
      </div>
      {importResult && (
        <div className="rounded-xl bg-green-500/10 border border-green-500/20 px-4 py-3 text-sm text-green-300">
          <p>{importResult.message}</p>
          {importResult.errors.length > 0 && (
            <ul className="mt-2 list-disc pl-5 text-amber-300">
              {importResult.errors.slice(0, 5).map((rowError) => (
                <li key={rowError}>{rowError}</li>
              ))}
              {importResult.errors.length > 5 && (
                <li>More invalid rows were omitted from this preview.</li>
              )}
            </ul>
          )}
          <p className="mt-2 text-xs text-slate-400">
            Supports extracted columns such as Business Name, Domain, Email, Phone, Website, City, Country, Rating, Reviews, and Maps URL.
          </p>
        </div>
      )}

      {/* Bulk actions bar */}
      <div className={`rounded-xl border px-4 py-3 flex flex-wrap items-center gap-3 transition ${
        selected.length ? "border-indigo-500/40 bg-indigo-500/10" : "border-white/8 bg-white/3"
      }`}>
        <span className="text-sm text-white font-medium">
          {selected.length ? `${selected.length} selected` : "Select leads to run bulk actions"}
        </span>
        {selected.length > 0 && (
          <>
            <button
              onClick={openBuilder}
              disabled={bulkBusy}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-medium transition"
            >
              <Layers size={14} /> Add to campaign…
            </button>
            <button
              onClick={emailNow}
              disabled={bulkBusy}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-indigo-500/40 bg-indigo-500/20 hover:bg-indigo-500/30 disabled:opacity-40 text-indigo-200 text-sm font-medium transition"
            >
              <Send size={14} /> Email now
            </button>
            <button
              onClick={queueCalls}
              disabled={bulkBusy}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-green-500/40 bg-green-500/20 hover:bg-green-500/30 disabled:opacity-40 text-green-200 text-sm font-medium transition"
            >
              <Phone size={14} /> Queue call
            </button>
            <button
              onClick={excludeSelected}
              disabled={bulkBusy}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-red-500/30 bg-red-500/10 hover:bg-red-500/20 disabled:opacity-40 text-red-300 text-sm font-medium transition"
            >
              <XCircle size={14} /> Exclude (do-not-contact)
            </button>
            <button
              onClick={unexcludeSelected}
              disabled={bulkBusy}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg border border-green-500/30 bg-green-500/10 hover:bg-green-500/20 disabled:opacity-40 text-green-300 text-sm font-medium transition"
            >
              <CheckCircle size={14} /> Re-enable
            </button>
            <button
              onClick={() => { setSelected([]); setBulkMsg(null); }}
              className="px-3 py-2 text-sm text-slate-400 hover:text-white transition"
            >
              Clear
            </button>
          </>
        )}
        {bulkMsg && (
          <span className="text-sm text-green-300">{bulkMsg}</span>
        )}
      </div>

      {/* Table */}
      <div className={`rounded-2xl border overflow-hidden ${activeBucket.color}`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/10">
                <th className="px-4 py-3 w-10">
                  <input
                    type="checkbox"
                    checked={leads.length > 0 && leads.every((l) => selected.includes(l.id))}
                    onChange={togglePage}
                    className="accent-indigo-500 h-4 w-4"
                  />
                </th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Business</th>
                {bucket === "call"    && <th className="text-left px-4 py-3 text-slate-400 font-medium">Phone</th>}
                {bucket === "email"   && <th className="text-left px-4 py-3 text-slate-400 font-medium">Email</th>}
                {bucket === "pending" && <th className="text-left px-4 py-3 text-slate-400 font-medium">Domain</th>}
                <th className="text-left px-4 py-3 text-slate-400 font-medium">City</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Reviews</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Last Review</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Maps</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">AI Note</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Status</th>
                <th className="text-left px-4 py-3 text-slate-400 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {loading ? (
                [...Array(8)].map((_, i) => (
                  <tr key={i}>
                    {[...Array(7)].map((_, j) => (
                      <td key={j} className="px-4 py-3">
                        <div className="h-4 bg-white/5 rounded animate-pulse" />
                      </td>
                    ))}
                  </tr>
                ))
              ) : leads.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-12 text-center text-slate-500">
                    No leads in this bucket{countryFilter !== "all" || cityFilter !== "all" ? " for the selected country/city" : ""} yet.
                  </td>
                </tr>
              ) : (
                leads.map((lead) => (
                  <tr key={lead.id} className={`transition-colors ${lead.do_not_contact ? "opacity-50" : "hover:bg-white/3"}`}>
                    <td className="px-4 py-3">
                      <input
                        type="checkbox"
                        checked={selected.includes(lead.id)}
                        onChange={() => toggleLead(lead.id)}
                        className="accent-indigo-500 h-4 w-4"
                      />
                    </td>
                    <td className="px-4 py-3 text-white font-medium max-w-[200px] truncate">
                      {lead.business_name || <span className="text-slate-500">—</span>}
                    </td>

                    {bucket === "call" && (
                      <td className="px-4 py-3 text-green-400 font-mono text-xs">{lead.phone}</td>
                    )}
                    {bucket === "email" && (
                      <td className="px-4 py-3 text-indigo-300 text-xs">{lead.email}</td>
                    )}
                    {bucket === "pending" && (
                      <td className="px-4 py-3 text-slate-300 text-xs">{lead.domain}</td>
                    )}

                    <td className="px-4 py-3 text-slate-400 text-xs">
                      <div>{lead.city || "—"}</div>
                      {lead.country && <div className="text-slate-600">{lead.country}</div>}
                    </td>

                    <td className="px-4 py-3 text-slate-400 text-xs">
                      {lead.reviews != null ? (
                        <span className="flex items-center gap-1">
                          <Star size={12} className="fill-yellow-400 text-yellow-400" /> {lead.rating?.toFixed(1) ?? "?"} <span className="text-slate-500">({lead.reviews})</span>
                        </span>
                      ) : "—"}
                    </td>

                    <td className="px-4 py-3 text-xs">
                      {lead.last_review_days != null ? (
                        <span className={`font-medium ${
                          lead.last_review_days <= 7   ? "text-green-400" :
                          lead.last_review_days <= 30  ? "text-yellow-400" :
                          lead.last_review_days <= 90  ? "text-orange-400" :
                                                         "text-red-400"
                        }`}>
                          {lead.last_review_days === 0 ? "Today" : `${lead.last_review_days}d ago`}
                        </span>
                      ) : <span className="text-slate-600">—</span>}
                    </td>

                    <td className="px-4 py-3">
                      {lead.maps_url ? (
                        <a href={lead.maps_url} target="_blank" rel="noopener noreferrer"
                          className="text-xs text-indigo-400 hover:text-indigo-300 underline">
                          View
                        </a>
                      ) : <span className="text-slate-600">—</span>}
                    </td>

                    <td className="px-4 py-3 max-w-[220px]">
                      {lead.improvement_note ? (
                        <button
                          onClick={() => setNoteModal({ name: lead.business_name || "Lead", note: lead.improvement_note! })}
                          className="text-xs text-yellow-300/80 leading-relaxed text-left hover:text-yellow-300 transition-colors"
                        >
                          {lead.improvement_note.slice(0, 60)}{lead.improvement_note.length > 60 ? "…" : ""}
                        </button>
                      ) : <span className="text-slate-600 text-xs">—</span>}
                    </td>

                    <td className="px-4 py-3">
                      <span className="flex items-center gap-1.5">
                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${STATUS_COLORS[lead.status] ?? "bg-slate-700 text-slate-300"}`}>
                          {lead.status}
                        </span>
                        {lead.do_not_contact && (
                          <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-900/40 text-red-300">
                            excluded
                          </span>
                        )}
                      </span>
                    </td>

                    <td className="px-4 py-3">
                      <select
                        value={lead.status}
                        onChange={(e) => updateStatus(lead.id, e.target.value)}
                        className="bg-white/5 border border-white/10 rounded px-2 py-1 text-xs text-slate-300 focus:outline-none"
                      >
                        {["new", "contacted", "snoozed", "replied", "converted", "dead"].map((s) => (
                          <option key={s} value={s} className="bg-[#1a1d2e]">{s}</option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {!loading && (
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPageChange={setPage} />
        )}
      </div>

      {/* Campaign builder modal */}
      {builderOpen && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={() => setBuilderOpen(false)}
        >
          <div
            className="bg-[#1a1d2e] border border-white/10 rounded-2xl p-6 max-w-2xl w-full max-h-[90vh] overflow-y-auto space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs text-slate-500 mb-0.5">New campaign</p>
                <h3 className="text-base font-semibold text-white">
                  Add {selected.length} selected lead(s) to a sequence
                </h3>
              </div>
              <button onClick={() => setBuilderOpen(false)} className="text-slate-400 hover:text-white text-xl leading-none">×</button>
            </div>

            <input
              type="text"
              placeholder="Campaign name (e.g. Vet Clinics — Sydney)"
              value={builderName}
              onChange={(e) => setBuilderName(e.target.value)}
              className="w-full bg-white/5 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
            />

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-xs text-slate-400 uppercase tracking-wider font-semibold">Steps (send order)</p>
                <button
                  onClick={() => {
                    const t = templates;
                    if (t.length) {
                      setBuilderSteps((prev) => [...prev, { template_id: t[0].id, delay_days: 7 }]);
                    } else {
                      setBulkMsg("No templates available — create one on the Outreach page first.");
                    }
                  }}
                  className="text-xs text-indigo-400 hover:text-indigo-300"
                >
                  + Add step
                </button>
              </div>
              {builderSteps.map((step, i) => (
                <div key={i} className="flex items-center gap-2">
                  <span className="w-5 text-xs text-slate-500 font-mono">{i + 1}</span>
                  <select
                    value={step.template_id}
                    onChange={(e) => {
                      const steps = [...builderSteps];
                      steps[i] = { ...steps[i], template_id: Number(e.target.value) };
                      setBuilderSteps(steps);
                    }}
                    className="flex-1 bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none"
                  >
                    <option value={0} className="bg-[#1a1d2e]">— pick template —</option>
                    {templates.map((t) => (
                      <option key={t.id} value={t.id} className="bg-[#1a1d2e]">{t.name}</option>
                    ))}
                  </select>
                  <label className="flex items-center gap-1.5 text-xs text-slate-400 whitespace-nowrap">
                    <input
                      type="number"
                      min={0}
                      value={step.delay_days}
                      onChange={(e) => {
                        const steps = [...builderSteps];
                        steps[i] = { ...steps[i], delay_days: Math.max(0, Number(e.target.value) || 0) };
                        setBuilderSteps(steps);
                      }}
                      className="w-16 bg-white/5 border border-white/10 rounded-lg px-2 py-2 text-sm text-white text-center focus:outline-none"
                    />
                    days later
                  </label>
                  <button
                    onClick={() => setBuilderSteps((prev) => prev.filter((_, j) => j !== i))}
                    className="text-slate-500 hover:text-red-400 text-lg leading-none px-1"
                    title="Remove step"
                  >
                    ×
                  </button>
                </div>
              ))}
              <p className="text-xs text-slate-500">
                Step 1 fires as soon as it&apos;s due; later steps wait their delay. Excluded leads and leads already in an active campaign are skipped automatically.
              </p>
            </div>

            <div className="flex justify-end gap-3">
              <button
                onClick={() => setBuilderOpen(false)}
                className="px-4 py-2.5 rounded-xl border border-white/10 bg-white/5 hover:bg-white/10 text-slate-300 text-sm transition"
              >
                Cancel
              </button>
              <button
                onClick={createCampaign}
                disabled={bulkBusy}
                className="px-5 py-2.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-semibold transition"
              >
                {bulkBusy ? "Creating…" : "Create campaign"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* AI Note modal */}
      {noteModal && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50 p-4"
          onClick={() => setNoteModal(null)}
        >
          <div
            className="bg-[#1a1d2e] border border-white/10 rounded-2xl p-6 max-w-lg w-full space-y-4"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-xs text-slate-500 mb-0.5">AI Improvement Note</p>
                <h3 className="text-base font-semibold text-white">{noteModal.name}</h3>
              </div>
              <button onClick={() => setNoteModal(null)} className="text-slate-400 hover:text-white text-xl leading-none">×</button>
            </div>
            <div className="bg-yellow-400/5 border border-yellow-400/20 rounded-xl px-4 py-4">
              <p className="text-sm text-yellow-200 leading-relaxed">{noteModal.note}</p>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={bulkConfirmation !== null}
        title={bulkConfirmation?.title ?? ""}
        description={bulkConfirmation?.description ?? ""}
        confirmLabel={bulkConfirmation?.confirmLabel ?? "Continue"}
        onConfirm={async () => {
          if (!bulkConfirmation) return false;
          await bulkConfirmation.onConfirm();
          return true;
        }}
        onError={(actionError) => setError(actionError instanceof Error ? actionError.message : "The action could not be completed.")}
        onClose={() => setBulkConfirmation(null)}
      />
    </div>
  );
}