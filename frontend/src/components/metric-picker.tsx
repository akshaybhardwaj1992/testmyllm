import { EngineBadge } from "@/components/status";
import { Tooltip } from "@/components/ui/overlay";
import { Checkbox, Input } from "@/components/ui/primitives";
import { applicability, metricKey, type MetricState } from "@/lib/metrics";
import { cn } from "@/lib/utils";
import type { InputName, MetricDef } from "@/types";

/**
 * Controlled metric selector grouped by engine. Metrics whose inputs are missing are disabled with
 * the reason (spec §4.1). `presentHint` explains where "present" comes from (e.g. a dataset version).
 */
export function MetricPicker({
  catalog,
  present,
  value,
  onChange,
  presentHint,
}: {
  catalog: MetricDef[];
  present: Set<InputName>;
  value: MetricState;
  onChange: (v: MetricState) => void;
  presentHint?: string;
}) {
  const set = (key: string, patch: Partial<MetricState[string]>) => onChange({ ...value, [key]: { ...value[key], ...patch } });
  return (
    <fieldset className="space-y-3">
      <legend className="text-sm font-medium">Metrics</legend>
      <p className="-mt-1 text-xs text-muted-foreground">
        {presentHint ?? "Only metrics whose inputs are available can run; the rest are disabled with the reason."}
      </p>
      {(["ragas", "deepeval"] as const).map((engine) => (
        <div key={engine} className="rounded-lg border">
          <div className="border-b bg-muted/30 px-3 py-1.5">
            <EngineBadge engine={engine} version={catalog[0]?.engines.find((e) => e.engine === engine)?.engine_version} />
          </div>
          <ul className="divide-y">
            {catalog
              .filter((d) => d.engines.some((e) => e.engine === engine))
              .map((d) => {
                const key = metricKey(d.name, engine);
                const reason = applicability(d, engine, present);
                const disabled = reason !== null;
                const v = value[key] ?? { selected: false, threshold: d.default_threshold };
                return (
                  <li key={key} className={cn("flex items-center gap-3 px-3 py-2", disabled && "opacity-55")}>
                    <Checkbox
                      id={`mp-${key}`}
                      checked={v.selected && !disabled}
                      disabled={disabled}
                      onCheckedChange={(c) => set(key, { selected: c === true })}
                    />
                    <Tooltip content={disabled ? reason : d.description}>
                      <label htmlFor={`mp-${key}`} className="flex-1 cursor-pointer text-sm">
                        {d.label}
                        {disabled && <span className="ml-2 text-xs text-muted-foreground">— {reason}</span>}
                      </label>
                    </Tooltip>
                    <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      {d.higher_is_better ? "≥" : "≤"}
                      <Input
                        type="number"
                        step="0.05"
                        min={0}
                        max={1}
                        disabled={disabled}
                        aria-label={`${d.label} (${engine}) threshold`}
                        className="h-7 w-16 px-2 text-xs"
                        value={v.threshold}
                        onChange={(e) => set(key, { threshold: Number(e.target.value) })}
                      />
                    </label>
                  </li>
                );
              })}
          </ul>
        </div>
      ))}
    </fieldset>
  );
}
