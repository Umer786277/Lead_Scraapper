"use client";

import { api, getActiveOrgId, setActiveOrgId } from "@/lib/api";
import { createClient } from "@/lib/supabase";
import { LEAD_STATUSES } from "@/lib/lead-status";
import {
  BarChart3,
  Building2,
  Inbox,
  LayoutDashboard,
  LogOut,
  Mail,
  Plus,
  Phone,
  Rocket,
  Settings,
  ShieldCheck,
  Target,
  UserRound,
  Users,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import type { Organization } from "@/lib/api";

const NAV = [
  { href: "/",          icon: LayoutDashboard, label: "Dashboard"  },
  { href: "/organization", icon: UserRound,     label: "Members"    },
  { href: "/platform", icon: ShieldCheck,       label: "Platform"   },
  { href: "/leads",     icon: Users,           label: "Leads"      },
  { href: "/replies",   icon: Inbox,           label: "Replies"    },
  { href: "/scrape",    icon: Rocket,          label: "Scrape"     },
  { href: "/outreach",  icon: Mail,            label: "Outreach"  },
  { href: "/analytics", icon: BarChart3,       label: "Analytics"  },
  { href: "/calls",     icon: Phone,           label: "Calls"      },
  { href: "/system",    icon: Settings,        label: "System"    },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const supabase = createClient();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [activeOrgId, setCurrentOrgId] = useState<string | null>(null);
  const [isSuperAdmin, setIsSuperAdmin] = useState(false);
  const [orgsLoading, setOrgsLoading] = useState(true);
  const [orgsError, setOrgsError] = useState<string | null>(null);
  const [newOrgName, setNewOrgName] = useState("");
  const [creatingOrg, setCreatingOrg] = useState(false);
  const [showOrgForm, setShowOrgForm] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadOrganizations() {
      try {
        const { items, is_super_admin: superAdmin } = await api.orgs.list();
        if (cancelled) return;
        setIsSuperAdmin(superAdmin);
        if (items.length === 0) {
          throw new Error("No organizations are available for this account.");
        }

        const storedOrgId = getActiveOrgId();
        const storedOrg = items.find((org) => org.id === storedOrgId);
        if (storedOrg) {
          setOrganizations(items);
          setCurrentOrgId(storedOrg.id);
          return;
        }

        if (storedOrgId) setActiveOrgId(null);
        const { org_id: currentOrgId } = await api.orgs.current();
        if (cancelled) return;
        if (!items.some((org) => org.id === currentOrgId)) {
          throw new Error("The current organization is not in your memberships.");
        }

        setActiveOrgId(currentOrgId);
        setOrganizations(items);
        setCurrentOrgId(currentOrgId);
        if (storedOrgId) window.location.reload();
      } catch (error) {
        if (!cancelled) {
          setOrgsError(error instanceof Error ? error.message : "Unable to load organizations.");
        }
      } finally {
        if (!cancelled) setOrgsLoading(false);
      }
    }

    void loadOrganizations();
    return () => {
      cancelled = true;
    };
  }, []);

  function switchOrganization(orgId: string) {
    if (orgId === activeOrgId) return;
    setActiveOrgId(orgId);
    setCurrentOrgId(orgId);
    window.location.reload();
  }

  async function createOrganization(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = newOrgName.trim();
    if (!name) return;
    setCreatingOrg(true);
    setOrgsError(null);
    try {
      const { id } = await api.orgs.create(name);
      setActiveOrgId(id);
      window.location.reload();
    } catch (error) {
      setOrgsError(error instanceof Error ? error.message : "Unable to create organization.");
      setCreatingOrg(false);
    }
  }

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <aside className="fixed inset-y-0 left-0 w-[220px] flex flex-col bg-gradient-to-b from-[#1a1d2e] to-[#111321] border-r border-white/5 z-40">
      {/* Brand */}
      <div className="flex items-center gap-2 px-5 py-5 border-b border-white/5">
        <Target size={22} className="text-indigo-400" />
        <span className="font-bold text-white text-lg tracking-tight">Missa</span>
      </div>

      {/* Organization */}
      <div className="px-3 py-3 border-b border-white/5">
        <label
          htmlFor="active-organization"
          className="mb-1.5 flex items-center gap-2 px-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500"
        >
          <Building2 size={14} />
          Organization
        </label>
        {organizations.length > 1 ? (
          <select
            id="active-organization"
            value={activeOrgId ?? ""}
            onChange={(event) => switchOrganization(event.target.value)}
            disabled={orgsLoading}
            className="w-full rounded-lg border border-white/10 bg-[#151827] px-2.5 py-2 text-sm text-slate-200 outline-none transition focus:border-indigo-500 disabled:opacity-60"
          >
            {organizations.map((org) => (
              <option key={org.id} value={org.id}>
                {org.name}
              </option>
            ))}
          </select>
        ) : (
          <div className="truncate rounded-lg border border-white/10 bg-[#151827] px-2.5 py-2 text-sm text-slate-200">
            {orgsLoading ? "Loading organizations..." : organizations[0]?.name ?? "Unavailable"}
          </div>
        )}
        {isSuperAdmin && !showOrgForm ? (
          <button
            type="button"
            onClick={() => setShowOrgForm(true)}
            className="mt-2 flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-xs font-medium text-indigo-300 transition hover:bg-white/5"
          >
            <Plus size={14} /> Create organization
          </button>
        ) : isSuperAdmin ? (
          <form onSubmit={createOrganization} className="mt-2 space-y-2">
            <input
              autoFocus
              value={newOrgName}
              onChange={(event) => setNewOrgName(event.target.value)}
              placeholder="Organization name"
              maxLength={100}
              className="w-full rounded-lg border border-white/10 bg-[#151827] px-2.5 py-2 text-sm text-slate-200 outline-none focus:border-indigo-500"
            />
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={creatingOrg || !newOrgName.trim()}
                className="flex-1 rounded-lg bg-indigo-600 px-2 py-1.5 text-xs font-semibold text-white transition hover:bg-indigo-500 disabled:opacity-50"
              >
                {creatingOrg ? "Creating..." : "Create"}
              </button>
              <button
                type="button"
                onClick={() => { setShowOrgForm(false); setNewOrgName(""); setOrgsError(null); }}
                disabled={creatingOrg}
                className="rounded-lg border border-white/10 px-2 py-1.5 text-xs text-slate-400 hover:bg-white/5"
              >
                Cancel
              </button>
            </div>
          </form>
        ) : null}
        {orgsError && (
          <p role="alert" className="mt-2 text-xs text-red-400">
            {orgsError}
          </p>
        )}
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {NAV.filter(({ href }) => href !== "/platform" || isSuperAdmin).map(({ href, icon: Icon, label }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <div key={href}>
              <Link
                href={href}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  active
                    ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/20"
                    : "text-slate-400 hover:text-white hover:bg-white/5"
                }`}
              >
                <Icon size={18} />
                {label}
              </Link>
              {href === "/leads" && active && (
                <div className="ml-8 mt-1 space-y-0.5 border-l border-white/10 pl-3">
                  <Link
                    href="/leads?status=all"
                    className="block rounded-md px-2 py-1.5 text-xs text-slate-400 transition hover:bg-white/5 hover:text-white"
                  >
                    All statuses
                  </Link>
                  {LEAD_STATUSES.map((status) => (
                    <Link
                      key={status}
                      href={`/leads?status=${status}`}
                      className="block rounded-md px-2 py-1.5 text-xs capitalize text-slate-400 transition hover:bg-white/5 hover:text-white"
                    >
                      {status}
                    </Link>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>

      {/* Sign out */}
      <div className="px-3 py-4 border-t border-white/5">
        <button
          onClick={signOut}
          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-400 hover:text-white hover:bg-white/5 transition-all"
        >
          <LogOut size={18} /> Sign out
        </button>
      </div>
    </aside>
  );
}
