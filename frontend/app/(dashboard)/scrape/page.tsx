"use client";

import { api } from "@/lib/api";
import SearchableSelect from "@/components/SearchableSelect";
import { CheckCircle2, Rocket } from "lucide-react";
import { useEffect, useRef, useState } from "react";

interface SearchRow {
  id: number;
  category: string;
  niche: string;
  city: string;
  country: string;
}
interface Schedule {
  id: number; niche: string; country: string;
  target_leads: number; status: string; created_at: string;
}
interface RunSummary {
  id: number; run_type: string; status: string;
  started_at: string | null; finished_at: string | null;
  input_count: number; output_count: number;
}

function formatWhen(value: string | null): string {
  if (!value) return "";
  const [date, time] = value.split("T");
  return `${date} ${(time ?? "").slice(0, 5)}`.trim();
}

const NICHE_OPTIONS = {
  Construction: [
    "General Contractors", "Roofing", "HVAC", "Plumbing", "Electrical", "Remodeling",
  ],
  Healthcare: [
    "Dental Clinics", "Hospitals", "Chiropractors", "Physiotherapy", "Veterinary Clinics",
  ],
  "Real Estate": [
    "Real Estate Agents", "Property Management", "Mortgage Brokers",
  ],
  Legal: ["Law Firms", "Immigration Lawyers", "Personal Injury Lawyers"],
  Automotive: ["Car Dealerships", "Auto Repair", "Car Wash", "Auto Detailing"],
  "Home Services": ["Cleaning", "Landscaping", "Pest Control", "Solar Installation"],
  Hospitality: ["Restaurants", "Hotels", "Cafes", "Catering"],
  "Professional Services": ["Accounting", "Consulting", "Marketing Agencies", "IT Companies"],
  "Beauty & Wellness": ["Salons", "Spas", "Gyms", "Fitness Studios"],
  Education: ["Schools", "Training Centers", "Tutoring Centers"],
};

const DEFAULT_ROWS: SearchRow[] = [{
  id: 1,
  category: "Healthcare",
  niche: "Veterinary Clinics",
  city: "",
  country: "United Kingdom",
}];

export default function ScrapePage() {
  const [rows, setRows] = useState<SearchRow[]>(DEFAULT_ROWS);
  const [countries, setCountries] = useState<string[]>([]);
  const [citiesByCountry, setCitiesByCountry] = useState<Record<string, string[]>>({});
  const [countriesLoading, setCountriesLoading] = useState(true);
  const [citiesLoading, setCitiesLoading] = useState<Record<string, boolean>>({});
  const cityCache = useRef(new Map<string, string[]>());
  const cityRequests = useRef(new Map<string, Promise<string[]>>());
  const nextRowId = useRef(2);
  const [maxLeads, setMaxLeads] = useState(20);
  const [enrich, setEnrich] = useState(true);
  const [headless, setHeadless] = useState(true);
  const [running, setRunning] = useState(false);
  const [runId, setRunId] = useState<number | null>(null);
  const [runLines, setRunLines] = useState<string[]>([]);
  const [runStatus, setRunStatus] = useState("");
  const [runOutput, setRunOutput] = useState(0);
  const logRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState("");

  // Recurring schedules
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [canManageSchedules, setCanManageSchedules] = useState(false);
  const [scheduling, setScheduling] = useState(false);
  const [scheduleMsg, setScheduleMsg] = useState("");
  const [scheduleOk, setScheduleOk] = useState(false);
  const [recentRuns, setRecentRuns] = useState<RunSummary[]>([]);

  useEffect(() => {
    void (async () => {
      try {
        const result = await api.pipeline.locationCountries();
        setCountries(result.countries);
        const initialCountry = DEFAULT_ROWS[0].country;
        if (result.countries.includes(initialCountry)) {
          const cities = await loadCitiesForCountry(initialCountry);
          setRows((current) => current.map((row) =>
            row.id === DEFAULT_ROWS[0].id && row.country === initialCountry
              ? { ...row, city: cities[0] ?? "" }
              : row,
          ));
        } else if (result.countries.length) {
          const country = result.countries[0];
          setRows((current) => current.map((row) =>
            row.id === DEFAULT_ROWS[0].id
              ? { ...row, country, city: "" }
              : row,
          ));
          const cities = await loadCitiesForCountry(country);
          setRows((current) => current.map((row) =>
            row.id === DEFAULT_ROWS[0].id && row.country === country
              ? { ...row, city: cities[0] ?? "" }
              : row,
          ));
        }
      } catch (loadError) {
        setError((loadError as Error).message);
      } finally {
        setCountriesLoading(false);
      }
    })();
    void loadSchedules();
    void loadRecentRuns();
    api.orgs.members.list()
      .then(({ can_manage }) => setCanManageSchedules(can_manage))
      .catch((loadError) => setError((loadError as Error).message));
  }, []);

  async function loadSchedules() {
    try {
      const list = await api.pipeline.schedules.list();
      setSchedules(list);
    } catch {
      // silent — schedules are secondary
    }
  }

  async function loadRecentRuns() {
    try {
      const list = await api.pipeline.runs();
      setRecentRuns(list);
    } catch {
      // silent — run history is secondary
    }
  }

  async function loadCitiesForCountry(country: string) {
    const cached = cityCache.current.get(country);
    if (cached) return cached;
    const existingRequest = cityRequests.current.get(country);
    if (existingRequest) return existingRequest;

    setCitiesLoading((current) => ({ ...current, [country]: true }));
    const request = api.pipeline.locationCities(country)
      .then(({ cities }) => {
        cityCache.current.set(country, cities);
        setCitiesByCountry((current) => ({ ...current, [country]: cities }));
        return cities;
      })
      .finally(() => {
        cityRequests.current.delete(country);
        setCitiesLoading((current) => ({ ...current, [country]: false }));
      });
    cityRequests.current.set(country, request);
    return request;
  }

  function updateCategory(id: number, category: string) {
    const firstNiche = NICHE_OPTIONS[category as keyof typeof NICHE_OPTIONS][0];
    setRows((current) => current.map((row) =>
      row.id === id ? { ...row, category, niche: firstNiche } : row,
    ));
  }

  function updateNiche(id: number, niche: string) {
    setRows((current) => current.map((row) =>
      row.id === id ? { ...row, niche } : row,
    ));
  }

  function updateCity(id: number, city: string) {
    setRows((current) => current.map((row) =>
      row.id === id ? { ...row, city } : row,
    ));
  }

  async function updateCountry(id: number, country: string) {
    const cached = cityCache.current.get(country);
    setRows((current) => current.map((row) =>
      row.id === id ? { ...row, country, city: cached?.[0] ?? "" } : row,
    ));
    if (cached) return;
    try {
      const cities = await loadCitiesForCountry(country);
      setRows((current) => current.map((row) =>
        row.id === id && row.country === country
          ? { ...row, city: cities[0] ?? "" }
          : row,
      ));
    } catch (loadError) {
      setError((loadError as Error).message);
    }
  }

  function addRow() {
    const id = nextRowId.current++;
    const category = "Construction";
    const country = rows[0]?.country || "United Kingdom";
    const city = cityCache.current.get(country)?.[0] ?? "";
    setRows((current) => [...current, {
      id,
      category,
      niche: NICHE_OPTIONS[category][0],
      city,
      country,
    }]);
    if (!city && country) {
      void loadCitiesForCountry(country)
        .then((cities) => setRows((current) => current.map((row) =>
          row.id === id ? { ...row, city: cities[0] ?? "" } : row,
        )))
        .catch((loadError) => setError((loadError as Error).message));
    }
  }
  function removeRow(id: number) {
    setRows((current) => current.filter((row) => row.id !== id));
  }

  const validRows = rows.filter((r) => r.niche.trim() && r.city.trim() && r.country.trim());

  // First valid row drives the schedule form (niche + country only)
  const scheduleSeed = validRows[0];

  async function handleRun() {
    setRunning(true); setError(""); setRunId(null);
    setRunLines([]); setRunStatus(""); setRunOutput(0);
    try {
      const res = await api.pipeline.run({
        searches: validRows.map(({ niche, city, country }) => ({ niche, city, country })),
        max_leads: maxLeads,
        headless,
        enrich_emails: enrich,
      });
      setRunId(res.run_id);
      void loadRecentRuns();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRunning(false);
    }
  }

  useEffect(() => {
    if (!runId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async () => {
      try {
        const data = await api.pipeline.runEvents(runId);
        if (!active) return;
        setRunLines(data.lines);
        setRunStatus(data.status);
        setRunOutput(data.output_count ?? 0);
        if (data.status !== "completed" && data.status !== "failed") {
          timer = setTimeout(poll, 1500);
        } else {
          api.pipeline.runs().then(setRecentRuns).catch(() => {});
        }
      } catch {
        if (active) timer = setTimeout(poll, 3000);
      }
    };

    timer = setTimeout(poll, 600);
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [runId]);

  useEffect(() => {
    const el = logRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [runLines]);

  async function handleSchedule() {
    if (!scheduleSeed) return;
    setScheduling(true); setScheduleMsg(""); setScheduleOk(false);
    try {
      const res = await api.pipeline.schedules.create({
        niche: scheduleSeed.niche,
        country: scheduleSeed.country,
        target_leads: maxLeads,
      });
      setScheduleOk(true);
      setScheduleMsg(
        `Scheduled — seeded ${res.queries_seeded} cities. Worker will rotate hourly.`
      );
      await loadSchedules();
    } catch (e) {
      setScheduleOk(false);
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
        <h1 className="flex items-center gap-2 text-2xl font-bold text-white">
          <Rocket className="text-orange-400" size={24} /> Scrape &amp; Enrich
        </h1>
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
          {rows.map((row) => (
            <div
              key={row.id}
              className="grid grid-cols-1 sm:grid-cols-2 gap-3 px-5 py-3 items-center"
            >
              <select
                aria-label="Business category"
                value={row.category}
                onChange={(e) => updateCategory(row.id, e.target.value)}
                className="w-full min-w-0 bg-[#1a1d2e] border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              >
                {Object.keys(NICHE_OPTIONS).map((category) => (
                  <option key={category} value={category}>{category}</option>
                ))}
              </select>
              <select
                aria-label="Business niche"
                value={row.niche}
                onChange={(e) => updateNiche(row.id, e.target.value)}
                className="w-full min-w-0 bg-[#1a1d2e] border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              >
                {NICHE_OPTIONS[row.category as keyof typeof NICHE_OPTIONS].map((niche) => (
                  <option key={niche} value={niche}>{niche}</option>
                ))}
              </select>
              <select
                aria-label="Country"
                value={row.country}
                disabled={countriesLoading || !countries.length}
                onChange={(e) => void updateCountry(row.id, e.target.value)}
                className="w-full min-w-0 bg-[#1a1d2e] border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500/40"
              >
                <option value="">
                  {countriesLoading ? "Loading countries…" : "Select country"}
                </option>
                {countries.map((country) => (
                  <option key={country} value={country}>{country}</option>
                ))}
              </select>
              <SearchableSelect
                ariaLabel="City"
                value={row.city}
                disabled={!row.country || citiesLoading[row.country] || !citiesByCountry[row.country]?.length}
                placeholder={
                  !row.country
                    ? "Select country"
                    : citiesLoading[row.country]
                      ? "Loading cities…"
                      : citiesByCountry[row.country]?.length
                        ? "Select city"
                        : "No cities available"
                }
                onChange={(city) => updateCity(row.id, city)}
                options={citiesByCountry[row.country] ?? []}
                searchPlaceholder="Search city…"
                emptyLabel="No cities match your search"
              />
              <button
                aria-label="Remove search target"
                onClick={() => removeRow(row.id)}
                disabled={rows.length === 1}
                className="justify-self-end text-slate-500 hover:text-red-400 disabled:opacity-20 text-lg leading-none transition sm:col-span-2"
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

      {/* Live run log */}
      {runId && (
        <div className="rounded-xl border border-white/8 bg-[#0d0f1a] overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2.5 border-b border-white/8">
            <span className="text-sm text-slate-300">
              Run <strong className="text-white">#{runId}</strong>
              <span className="ml-2 text-xs uppercase tracking-wide text-slate-500">
                {runStatus || "starting"}
              </span>
            </span>
            <span className="text-xs text-slate-500">
              {runOutput} lead{runOutput === 1 ? "" : "s"} saved
            </span>
          </div>
          <div
            ref={logRef}
            className="max-h-72 overflow-y-auto px-4 py-3 font-mono text-xs leading-relaxed text-slate-300"
          >
            {runLines.length === 0 ? (
              <p className="text-slate-500">Waiting for the scraper to report…</p>
            ) : (
              runLines.map((line, index) => (
                <div key={index} className="whitespace-pre-wrap break-words">{line}</div>
              ))
            )}
          </div>
          {(runStatus === "completed" || runStatus === "failed") && (
            <div className="border-t border-white/8 px-4 py-2.5 text-xs">
              <a href="/leads" className="text-indigo-400 hover:text-indigo-300 underline">
                View leads
              </a>
            </div>
          )}
        </div>
      )}
      {/* Recent runs — click one to load its persisted log */}
      {recentRuns.length > 0 && (
        <div className="rounded-xl border border-white/8 bg-white/3 overflow-hidden">
          <div className="flex items-center justify-between border-b border-white/8 px-4 py-2.5">
            <span className="text-sm font-medium text-slate-300">Recent runs</span>
            <button
              onClick={() => void loadRecentRuns()}
              className="text-xs text-indigo-400 hover:text-indigo-300 transition"
            >Refresh</button>
          </div>
          <div className="max-h-56 divide-y divide-white/5 overflow-y-auto">
            {recentRuns.map((run) => (
              <button
                key={run.id}
                onClick={() => {
                  setRunLines([]);
                  setRunStatus("");
                  setRunOutput(0);
                  setRunId(run.id);
                }}
                className={`flex w-full items-center justify-between gap-3 px-4 py-2.5 text-left text-xs transition hover:bg-white/5 ${
                  run.id === runId ? "bg-indigo-500/10" : ""
                }`}
              >
                <span className="min-w-0 truncate text-slate-300">
                  <strong className="text-white">#{run.id}</strong>
                  <span className="ml-2 text-slate-500">
                    {run.run_type.replace(/_/g, " ")}
                  </span>
                  <span className="ml-2 text-slate-500">
                    {formatWhen(run.started_at)}
                  </span>
                </span>
                <span className="flex shrink-0 items-center gap-3">
                  <span className="text-slate-400">
                    {run.output_count} lead{run.output_count === 1 ? "" : "s"}
                  </span>
                  <span className={`rounded-full px-2 py-0.5 ${
                    run.status === "completed"
                      ? "bg-green-900/40 text-green-300"
                      : run.status === "failed"
                        ? "bg-red-900/40 text-red-300"
                        : "bg-indigo-900/40 text-indigo-300"
                  }`}>{run.status}</span>
                </span>
              </button>
            ))}
          </div>
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
        {running ? "Starting pipeline…" : (
          <span className="inline-flex items-center gap-2">
            <Rocket size={16} /> Run — {validRows.length} search(es) × {maxLeads} leads{enrich ? " + emails" : ""}
          </span>
        )}
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

        {canManageSchedules && (
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
        )}
        {!canManageSchedules && (
          <p className="mb-4 text-xs text-slate-500">
            Only organization admins can create or pause recurring schedules.
          </p>
        )}

        {canManageSchedules && scheduleMsg && (
          <div className={`mt-3 flex items-center gap-2 rounded-xl px-4 py-3 text-sm ${
            scheduleOk
              ? "bg-green-500/10 border border-green-500/20 text-green-400"
              : "bg-red-500/10 border border-red-500/20 text-red-400"
          }`}>
            {scheduleOk && <CheckCircle2 size={16} />}
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
                {canManageSchedules && s.status === "active" && (
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
