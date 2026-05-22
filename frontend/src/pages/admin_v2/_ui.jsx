// Wiederverwendbare UI-Bausteine für das Admin-Dashboard.
// Dark-Theme — passend zum Rest der App.

import React from "react";

export function PageHeader({ title, subtitle, action }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-3 mb-6">
      <div>
        <h1 className="text-[28px] md:text-[32px] font-bold tracking-tight leading-none text-white">
          {title}
        </h1>
        {subtitle && <p className="mt-1.5 text-[14px] text-zinc-400">{subtitle}</p>}
      </div>
      {action}
    </div>
  );
}

export function Card({ children, className = "", padded = true }) {
  return (
    <div
      className={`rounded-2xl ${padded ? "p-5" : ""} ${className}`}
      style={{
        background: "rgba(255,255,255,0.03)",
        border: "1px solid rgba(255,255,255,0.08)",
        boxShadow: "0 1px 2px rgba(0,0,0,0.3)",
      }}
    >
      {children}
    </div>
  );
}

export function StatCard({ label, value, hint, color = "blue" }) {
  const ring = {
    blue: "from-blue-500 to-indigo-500",
    green: "from-emerald-500 to-teal-500",
    purple: "from-purple-500 to-pink-500",
    orange: "from-orange-500 to-amber-500",
    pink: "from-pink-500 to-rose-500",
    gray: "from-zinc-500 to-zinc-700",
    red: "from-red-500 to-rose-500",
  }[color] || "from-blue-500 to-indigo-500";
  return (
    <Card>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-[12px] font-medium text-zinc-500 uppercase tracking-wide">{label}</div>
          <div className="mt-2 text-[28px] font-bold tracking-tight tabular-nums text-white">{value}</div>
          {hint && <div className="mt-1 text-[12px] text-zinc-500">{hint}</div>}
        </div>
        <div className={`w-2.5 h-10 rounded-full bg-gradient-to-b ${ring}`} />
      </div>
    </Card>
  );
}

export function Badge({ children, tone = "gray" }) {
  const tones = {
    gray:   "bg-white/5 text-zinc-300 border border-white/10",
    green:  "bg-emerald-500/15 text-emerald-300 border border-emerald-500/30",
    red:    "bg-red-500/15 text-red-300 border border-red-500/30",
    blue:   "bg-blue-500/15 text-blue-300 border border-blue-500/30",
    purple: "bg-purple-500/15 text-purple-300 border border-purple-500/30",
    orange: "bg-orange-500/15 text-orange-300 border border-orange-500/30",
    yellow: "bg-amber-500/15 text-amber-300 border border-amber-500/30",
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-medium ${tones[tone] || tones.gray}`}>
      {children}
    </span>
  );
}

export function Button({ children, variant = "primary", size = "md", className = "", ...rest }) {
  const sizes = {
    sm: "h-8 px-3 text-[12.5px]",
    md: "h-10 px-4 text-[14px]",
    lg: "h-11 px-5 text-[15px]",
  };
  const variants = {
    primary:  "bg-[var(--accent-red)] text-white hover:bg-[var(--accent-red-hover)] shadow-sm",
    secondary: "bg-white/10 text-white hover:bg-white/15 border border-white/10",
    danger:   "bg-red-600 text-white hover:bg-red-700 shadow-sm",
    ghost:    "text-zinc-300 hover:bg-white/5",
    outline:  "bg-transparent border border-white/15 text-white hover:bg-white/5",
  };
  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded-xl font-medium transition-all disabled:opacity-50 disabled:cursor-not-allowed ${sizes[size]} ${variants[variant]} ${className}`}
      {...rest}
    >
      {children}
    </button>
  );
}

export function Spinner({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" className="animate-spin text-zinc-500">
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" fill="none" strokeDasharray="40 56" />
    </svg>
  );
}

export function EmptyState({ title, hint }) {
  return (
    <div className="text-center py-16">
      <div className="text-[15px] font-semibold text-zinc-200">{title}</div>
      {hint && <div className="mt-1 text-[13px] text-zinc-500">{hint}</div>}
    </div>
  );
}

export function fmtDate(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("de-DE", {
      day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function fmtNum(n) {
  if (n === null || n === undefined || n === "") return "—";
  try { return Number(n).toLocaleString("de-DE"); } catch { return String(n); }
}
