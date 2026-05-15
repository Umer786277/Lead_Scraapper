"use client";

import { createClient } from "@/lib/supabase";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

const NAV = [
  { href: "/",          icon: "🏠", label: "Dashboard"  },
  { href: "/leads",     icon: "👥", label: "Leads"      },
  { href: "/scrape",    icon: "🚀", label: "Scrape"     },
  { href: "/outreach",  icon: "✉️",  label: "Outreach"  },
  { href: "/analytics", icon: "📊", label: "Analytics"  },
  { href: "/calls",     icon: "📞", label: "Calls"      },
  { href: "/system",    icon: "⚙️",  label: "System"    },
];

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const supabase = createClient();

  async function signOut() {
    await supabase.auth.signOut();
    router.push("/login");
    router.refresh();
  }

  return (
    <aside className="fixed inset-y-0 left-0 w-[220px] flex flex-col bg-gradient-to-b from-[#1a1d2e] to-[#111321] border-r border-white/5 z-40">
      {/* Brand */}
      <div className="flex items-center gap-2 px-5 py-5 border-b border-white/5">
        <span className="text-xl">🎯</span>
        <span className="font-bold text-white text-lg tracking-tight">LeadFlow</span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {NAV.map(({ href, icon, label }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                active
                  ? "bg-indigo-600/20 text-indigo-300 border border-indigo-500/20"
                  : "text-slate-400 hover:text-white hover:bg-white/5"
              }`}
            >
              <span className="text-base">{icon}</span>
              {label}
            </Link>
          );
        })}
      </nav>

      {/* Sign out */}
      <div className="px-3 py-4 border-t border-white/5">
        <button
          onClick={signOut}
          className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-400 hover:text-white hover:bg-white/5 transition-all"
        >
          <span>🚪</span> Sign out
        </button>
      </div>
    </aside>
  );
}
