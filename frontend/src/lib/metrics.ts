import type { InputName, MetricDef } from "@/types";

export const metricKey = (metric: string, engine: string) => `${metric}|${engine}`;

export const INPUT_LABEL: Record<string, string> = {
  question: "question",
  response: "model response",
  reference: "reference answer",
  context: "context",
  criteria: "evaluation criteria",
};

/** null = can run; otherwise the human-readable reason it cannot (mirrors backend applicability.check). */
export function applicability(def: MetricDef, engine: string, present: Set<InputName>): string | null {
  const impl = def.engines.find((e) => e.engine === engine);
  if (!impl) return `Not available in ${engine}`;
  const missing = impl.requires.filter((r) => !present.has(r as InputName));
  return missing.length ? `Needs ${missing.map((m) => INPUT_LABEL[m] ?? m).join(", ")}` : null;
}

export type MetricState = Record<string, { selected: boolean; threshold: number }>;

export function defaultMetricState(catalog: MetricDef[], selected: string[] = ["answer_relevancy"]): MetricState {
  const m: MetricState = {};
  catalog.forEach((d) =>
    d.engines.forEach((e) => {
      m[metricKey(d.name, e.engine)] = { selected: selected.includes(d.name), threshold: d.default_threshold };
    }),
  );
  return m;
}

export function selectedMetrics(state: MetricState, catalog: MetricDef[], present: Set<InputName>) {
  return Object.entries(state)
    .filter(([, m]) => m.selected)
    .map(([k, m]) => {
      const [metric, engine] = k.split("|");
      return { metric, engine: engine as "ragas" | "deepeval", threshold: Number(m.threshold) };
    })
    .filter((m) => {
      const def = catalog.find((d) => d.name === m.metric);
      return def && applicability(def, m.engine, present) === null;
    });
}

const EXTRA_LABELS: Record<string, string> = { attack_resisted: "Attack resisted" };

export const metricLabel = (catalog: MetricDef[] | undefined, name: string) =>
  catalog?.find((m) => m.name === name)?.label ?? EXTRA_LABELS[name] ?? name;
