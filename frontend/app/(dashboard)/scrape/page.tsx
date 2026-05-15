"use client";

import { api } from "@/lib/api";
import { useEffect, useState } from "react";

interface SearchRow { niche: string; city: string; country: string; }
interface Schedule {
  id: number; niche: string; country: string;
  target_leads: number; status: string; created_at: string;
}

const COUNTRY_CITIES: Record<string, string[]> = {
  UK: [
    "London", "Manchester", "Birmingham", "Leeds", "Glasgow", "Sheffield",
    "Edinburgh", "Liverpool", "Bristol", "Cardiff", "Leicester", "Coventry",
    "Nottingham", "Newcastle upon Tyne", "Brighton", "Southampton",
    "Oxford", "Cambridge", "Aberdeen", "Belfast",
  ],
  USA: [
    "New York", "Los Angeles", "Chicago", "Houston", "Phoenix", "Philadelphia",
    "San Antonio", "San Diego", "Dallas", "San Jose", "Austin", "Jacksonville",
    "Fort Worth", "Columbus", "Charlotte", "Indianapolis", "San Francisco",
    "Seattle", "Denver", "Nashville", "Las Vegas", "Portland", "Miami", "Atlanta",
  ],
  Australia: [
    "Sydney", "Melbourne", "Brisbane", "Perth", "Adelaide", "Gold Coast",
    "Canberra", "Newcastle", "Wollongong", "Sunshine Coast", "Geelong",
    "Townsville", "Hobart", "Cairns", "Darwin", "Ballarat", "Bendigo",
    "Albury", "Launceston", "Mackay",
  ],
};

const COUNTRIES = Object.keys(COUNTRY_CITIES);

const DEFAULT_ROWS: SearchRow[] = [{ niche: "pet clinics", city: "London", country: "UK" }];

export default function ScrapePage() {
  const [rows, setRows] = useState<SearchRow[]>(DEFAULT_ROWS);
  const [maxLeads, setMaxLeads] = useState(20);
  const [enrich, setEnrich] = useState(true);
  const [headless, setHeadless] = useState(true);
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<number | null>(null);
  const [error, setError] = useState("");

  // Recurring schedules
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [scheduling, setScheduling] = useState(false);
  const [scheduleMsg, setScheduleMsg] = useState("");

  useEffect(() => { loadSchedules(); }, []);

  async function loadSchedules() {
    try {
      const list = await api.pipeline.schedules.list();
      setSchedules(list);
    } catch {
      // silent — schedules are secondary
    }
  }

  function updateRow(i: number, field: keyof SearchRow, val: string) {
    setRows((prev) => prev.map((r, idx) => {
      if (idx !== i) return r;
      if (field === "country") {
        // Reset city to first city of the new country
        return { ...r, country: val, city: COUNTRY_CITIES[val]?.[0] ?? "" };
      }
      return { ...r, [field]: val };
    }));
  }
  function addRow() {
    setRows((prev) => [...prev, { niche: "", city: COUNTRY_CITIES["UK"][0], country: "UK" }]);
  }
  function removeRow(i: number) { setRows((prev) => prev.filter((_, idx) => idx !== i)); }

  const validRows = rows.filter((r) => r.niche.trim() && r.city.trim() && r.country.trim());

  // First valid row drives the schedule form (niche + country only)
  const scheduleSeed = validRows[0];

  async function handleRun() {
    setRunning(true); setError(""); setRunId(null);
    try {
      const res = await api.pipeline.run({
        searches: validRows,
        max_leads: maxLeads,
        headless,
        enrich_emails: enrich,
      });
      setRunId(res.run_id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  async function handleSchedule() {
    if (!scheduleSeed) return;
    setScheduling(true); setScheduleMsg("");
    try {
      const res = await api.pipeline.schedules.create({
        niche: scheduleSeed.niche,
        country: scheduleSeed.country,
        target_leads: maxLeads,
      });
      setScheduleMsg(
        `✅ Scheduled — seeded ${res.queries_seeded} cities. Worker will rotate hourly.`
      );
      await loadSchedules();
    } catch (e) {
      setScheduleMsg((e as Error).message);
    } finally {
      setScheduling(false);
    }
  }

  async function handleCancel(id: number) {
    try {
      await api.pipeline.schedules.cancel(id);
      await loadSchedules();
    } catch (e) {
      setScheduleMsg((e as Error).message);
    }
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div>
        <h1 className="text-2xl font-bold text-white">🚀 Scrape &amp; Enrich</h1>
        <p className="text-slate-400 text-sm mt-1">
          Google Maps → business leads → emails extracted automatically.
        </p>
      </div>

      {/* Search rows */}
      <div className="rounded-2xl border border-white/8 bg-white/3 overflow-hidden">
        <div className="px-5 py-3 border-b border-white/8">
          <span className="text-sm font-medium text-slate-300">Search targets</span>
          <span className="text-xs text-slate-500 ml-2">— each row becomes one Google Maps query</span>
        </div>
        <div className="divide-y divide-white/5">
          {rows.map((row, i) => (
            <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-3 px-5 py-3 items-center">
              <input
                placeholder="Niche (e.g. pet clinics)"
                value={row.niche}
                onChange={(e) => updateRow(i, "niche", e.target.value)}
                className="bg-white/5 border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              />
              {/* Country dropdown */}
              <select
                value={row.country}
                onChange={(e) => updateRow(i, "country", e.target.value)}
                className="bg-[#1a1d2e] border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              >
                {COUNTRIES.map((c) => (
                  <option key={c} value={c}>{c}</option>
                ))}
              </select>
              {/* City dropdown — options depend on selected country */}
              <select
                value={row.city}
                onChange={(e) => updateRow(i, "city", e.target.value)}
                className="bg-[#1a1d2e] border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              >
                {(COUNTRY_CITIES[row.country] ?? []).map((city) => (
                  <option key={city} value={city}>{city}</option>
                ))}
              </select>
              <button
                onClick={() => removeRow(i)}
                disabled={rows.length === 1}
                className="text-slate-500 hover:text-red-400 disabled:opacity-20 text-lg leading-none transition"
              >×</button>
            </div>
          ))}
        </div>
        <div className="px-5 py-3 border-t border-white/8">
          <button
            onClick={addRow}
            className="text-sm text-indigo-400 hover:text-indigo-300 transition"
          >+ Add row</button>
        </div>
      </div>

      {/* Settings */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="col-span-2 rounded-xl border border-white/8 bg-white/3 px-5 py-4">
          <label className="block text-xs text-slate-400 mb-2">
            Max leads per search: <span className="text-white font-bold">{maxLeads}</span>
          </label>
          <input
            type="range" min={5} max={60} value={maxLeads}
            onChange={(e) => setMaxLeads(Number(e.target.value))}
            className="w-full accent-indigo-500"
          />
          <div className="flex justify-between text-xs text-slate-500 mt-1"><span>5</span><span>60</span></div>
        </div>

        {[
          { label: "Extract emails", val: enrich,   set: setEnrich },
          { label: "Headless browser", val: headless, set: setHeadless },
        ].map(({ label, val, set }) => (
          <div key={label} className="rounded-xl border border-white/8 bg-white/3 px-5 py-4 flex items-center justify-between">
            <span className="text-sm text-slate-300">{label}</span>
            <button
              onClick={() => set(!val)}
              className={`relative w-11 h-6 rounded-full transition-colors ${val ? "bg-indigo-600" : "bg-slate-700"}`}
            >
              <span className={`absolute top-0.5 left-0.5 w-5 h-5 bg-white rounded-full shadow transition-transform ${val ? "translate-x-5" : ""}`} />
            </button>
          </div>
        ))}
      </div>

      {/* Status / result */}
      {runId && (
        <div className="rounded-xl bg-green-500/10 border border-green-500/20 px-5 py-4 text-green-400">
          Run <strong>#{runId}</strong> started in background. Check the{" "}
          <a href="/leads" className="underline">Leads page</a> for results.
        </div>
      )}
      {error && (
        <div className="rounded-xl bg-red-500/10 border border-red-500/20 px-5 py-4 text-red-400 text-sm">
          {error.includes("fetch") ? "Cannot reach the API server — is FastAPI running on port 8000?" : error}
        </div>
      )}

      {/* Run button */}
      <button
        onClick={handleRun}
        disabled={running || validRows.length === 0}
        className="w-full py-3 rounded-xl bg-gradient-to-r from-orange-500 to-red-500 hover:from-orange-400 hover:to-red-400 disabled:opacity-40 text-white font-bold text-sm shadow-lg shadow-orange-500/20 transition-all"
      >
        {running ? "Starting pipeline…" : `🚀  Run — ${validRows.length} search(es) × ${maxLeads} leads${enrich ? " + emails" : ""}`}
      </button>

      {/* ── Recurring schedules ──────────────────────────────── */}
      <div className="pt-6 border-t border-white/8">
        <h2 className="text-sm font-semibold text-slate-400 uppercase tracking-wider mb-1">
          Recurring schedule
        </h2>
        <p className="text-xs text-slate-500 mb-4">
          Worker rotates through cities of the country every hour. Saturated cities
          back off automatically; fresh ones get prioritized.
        </p>

        <div className="rounded-2xl border border-white/8 bg-white/3 px-5 py-4 flex flex-col md:flex-row md:items-center gap-3">
          <div className="text-sm text-slate-300 flex-1">
            {scheduleSeed ? (
              <>
                Schedule <span className="text-white font-medium">{scheduleSeed.niche}</span> across{" "}
                <span className="text-white font-medium">{scheduleSeed.country}</span> ·
                target <span className="text-white font-medium">{maxLeads}</span> leads/city
              </>
            ) : (
              <span className="text-slate-500">
                Fill the first row above (niche + country) to enable scheduling.
              </span>
            )}
          </div>
          <button
            onClick={handleSchedule}
            disabled={scheduling || !scheduleSeed}
            className="px-5 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 text-white text-sm font-semibold transition shrink-0"
          >
            {scheduling ? "Scheduling…" : "Schedule recurring"}
          </button>
        </div>

        {scheduleMsg && (
          <div className={`mt-3 rounded-xl px-4 py-3 text-sm ${
            scheduleMsg.startsWith("✅")
              ? "bg-green-500/10 border border-green-500/20 text-green-400"
              : "bg-red-500/10 border border-red-500/20 text-red-400"
          }`}>
            {scheduleMsg}
          </div>
        )}

        {schedules.length > 0 && (
          <div className="mt-5 rounded-2xl border border-white/8 overflow-hidden divide-y divide-white/5">
            {schedules.map((s) => (
              <div key={s.id} className="flex items-center justify-between px-5 py-3">
                <div className="text-sm">
                  <span className="text-white font-medium">{s.niche}</span>
                  <span className="text-slate-500"> · </span>
                  <span className="text-slate-300">{s.country}</span>
                  <span className="text-slate-500"> · </span>
                  <span className="text-slate-400">{s.target_leads} leads/city</span>
                  <span className={`ml-3 text-xs px-2 py-0.5 rounded-full ${
                    s.status === "active"
                      ? "bg-green-900/40 text-green-300"
                      : "bg-slate-700 text-slate-400"
                  }`}>{s.status}</span>
                </div>
                {s.status === "active" && (
                  <button
                    onClick={() => handleCancel(s.id)}
                    className="text-xs text-slate-400 hover:text-red-400 transition"
                  >
                    Pause
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
