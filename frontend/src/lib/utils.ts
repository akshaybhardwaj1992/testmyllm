import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const fmtScore = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));

export function fmtUsd(v: number | string | null | undefined, empty = "unknown") {
  if (v == null) return empty;
  const n = typeof v === "string" ? Number(v) : v;
  if (n === 0) return "$0.00";
  // Sub-cent amounts (per-answer costs of cheap models) keep 2 significant digits, e.g. $0.0000031
  if (n < 0.01) return `$${n.toLocaleString("en-US", { maximumSignificantDigits: 2, maximumFractionDigits: 10 })}`;
  return `$${n.toFixed(2)}`;
}

export function fmtDate(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

export function fmtDuration(start?: string | null, end?: string | null) {
  if (!start) return "—";
  const ms = (end ? new Date(end) : new Date()).getTime() - new Date(start).getTime();
  const s = Math.max(0, Math.round(ms / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
