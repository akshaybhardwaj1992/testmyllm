/** Status chips and score display. Colour is never the only signal: every status has an icon and text. */
import { AlertTriangle, Check, Clock, Loader2, Minus, X, Ban } from "lucide-react";

import { Tooltip } from "@/components/ui/overlay";
import { cn, fmtScore } from "@/lib/utils";

export type AnyStatus =
  | "passed"
  | "failed"
  | "not_applicable"
  | "error"
  | "running"
  | "queued"
  | "pending"
  | "completed"
  | "cancelled";

const STYLES: Record<AnyStatus, { label: string; cls: string; Icon: typeof Check; spin?: boolean }> = {
  passed: { label: "Passed", cls: "bg-pass-bg text-pass", Icon: Check },
  completed: { label: "Completed", cls: "bg-pass-bg text-pass", Icon: Check },
  failed: { label: "Failed", cls: "bg-fail-bg text-fail", Icon: X },
  error: { label: "Error", cls: "bg-warn-bg text-warn", Icon: AlertTriangle },
  not_applicable: { label: "N/A", cls: "bg-na-bg text-na", Icon: Minus },
  running: { label: "Running", cls: "bg-info-bg text-info", Icon: Loader2, spin: true },
  queued: { label: "Queued", cls: "bg-na-bg text-na", Icon: Clock },
  pending: { label: "Pending", cls: "bg-na-bg text-na", Icon: Clock },
  cancelled: { label: "Cancelled", cls: "bg-na-bg text-na", Icon: Ban },
};

export function StatusChip({ status, className, label }: { status: AnyStatus; className?: string; label?: string }) {
  const s = STYLES[status] ?? STYLES.pending;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium",
        s.cls,
        className,
      )}
    >
      <s.Icon className={cn("size-3.5", s.spin && "animate-spin")} aria-hidden strokeWidth={2.5} />
      {label ?? s.label}
    </span>
  );
}

export function EngineBadge({ engine, version }: { engine: string; version?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-1.5 py-0.5 font-mono text-[11px] font-medium",
        engine === "ragas" ? "bg-[var(--chart-1)]/12 text-[var(--chart-1)]" : "bg-[var(--chart-2)]/14 text-[var(--chart-2)]",
      )}
    >
      {engine}
      {version ? <span className="ml-1 opacity-70">{version}</span> : null}
    </span>
  );
}

/**
 * Score with threshold marker. Hover shows engine, version, judge and repeats (mean ± std).
 * N/A renders as "N/A" with the missing-input reason; errors render as an error, never as 0.
 */
export function Score({
  score,
  threshold,
  higherIsBetter = true,
  status,
  engine,
  engineVersion,
  judgeModel,
  n,
  std,
  reason,
  compact,
}: {
  score: number | null | undefined;
  threshold?: number | null;
  higherIsBetter?: boolean;
  status: "passed" | "failed" | "not_applicable" | "error";
  engine?: string;
  engineVersion?: string;
  judgeModel?: string | null;
  n?: number;
  std?: number | null;
  reason?: string | null;
  compact?: boolean;
}) {
  if (status === "not_applicable") {
    return (
      <Tooltip content={reason ?? "Not applicable"}>
        <span tabIndex={0} className="inline-flex items-center gap-1 text-sm text-na">
          <Minus className="size-3.5" aria-hidden /> N/A
        </span>
      </Tooltip>
    );
  }
  if (status === "error" || score == null) {
    return (
      <Tooltip content={reason ?? "The judge call failed. Errors are excluded from averages and gates."}>
        <span tabIndex={0} className="inline-flex items-center gap-1 text-sm text-warn">
          <AlertTriangle className="size-3.5" aria-hidden /> Error
        </span>
      </Tooltip>
    );
  }
  const pct = Math.max(0, Math.min(1, score)) * 100;
  const tip = (
    <div className="space-y-0.5">
      {engine && (
        <div>
          Engine: {engine} {engineVersion}
        </div>
      )}
      {judgeModel && <div>Judge: {judgeModel}</div>}
      {threshold != null && (
        <div>
          Threshold: {higherIsBetter ? "≥" : "≤"} {threshold.toFixed(2)}
        </div>
      )}
      {n != null && n > 1 && (
        <div>
          {n} repeats: {score.toFixed(3)} ± {(std ?? 0).toFixed(3)}
        </div>
      )}
    </div>
  );
  return (
    <Tooltip content={tip}>
      <span tabIndex={0} className="inline-flex flex-col gap-1">
        <span className="inline-flex items-center gap-1.5">
          <span className={cn("tabular font-semibold", status === "passed" ? "text-pass" : "text-fail")}>
            {fmtScore(score)}
          </span>
          {n != null && n > 1 && (
            <span className="tabular text-xs text-muted-foreground">±{(std ?? 0).toFixed(2)}</span>
          )}
          {status === "passed" ? (
            <Check className="size-3.5 text-pass" aria-label="passed" />
          ) : (
            <X className="size-3.5 text-fail" aria-label="failed" />
          )}
        </span>
        {!compact && (
          <span className="relative block h-1.5 w-20 rounded-full bg-muted" aria-hidden>
            <span
              className={cn("absolute inset-y-0 left-0 rounded-full", status === "passed" ? "bg-pass" : "bg-fail")}
              style={{ width: `${pct}%` }}
            />
            {threshold != null && (
              <span
                className="absolute -top-0.5 h-2.5 w-0.5 rounded bg-foreground"
                style={{ left: `calc(${threshold * 100}% - 1px)` }}
              />
            )}
          </span>
        )}
      </span>
    </Tooltip>
  );
}
