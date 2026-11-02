import type { LucideIcon } from "lucide-react";

interface StatCardProps {
  label: string;
  value: string | number;
  sub?: string;
  color?: "indigo" | "green" | "yellow" | "red" | "default";
  icon?: LucideIcon;
}

const colorMap = {
  indigo:  "border-indigo-500/20  bg-indigo-500/5",
  green:   "border-green-500/20   bg-green-500/5",
  yellow:  "border-yellow-500/20  bg-yellow-500/5",
  red:     "border-red-500/20     bg-red-500/5",
  default: "border-white/8        bg-white/3",
};

const iconColorMap = {
  indigo:  "text-indigo-400",
  green:   "text-green-400",
  yellow:  "text-yellow-400",
  red:     "text-red-400",
  default: "text-slate-400",
};

export default function StatCard({ label, value, sub, color = "default", icon: Icon }: StatCardProps) {
  return (
    <div className={`rounded-2xl border p-5 ${colorMap[color]}`}>
      <p className="flex items-center gap-1.5 text-xs text-slate-400 mb-1">
        {Icon && <Icon size={14} className={iconColorMap[color]} />}
        {label}
      </p>
      <p className="text-3xl font-bold text-white tracking-tight">{value}</p>
      {sub && <p className="text-xs text-slate-500 mt-1">{sub}</p>}
    </div>
  );
}
