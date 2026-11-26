import { AlertTriangle, CheckCircle2, FileUp, UploadCloud, XCircle } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/overlay";
import { Checkbox, Field, Input, NativeSelect } from "@/components/ui/primitives";
import { uploadDataset } from "@/hooks/queries";
import { cn } from "@/lib/utils";
import { ApiError, errorMessage } from "@/services/api";
import type { Dataset, UploadOut } from "@/types";
import { useQueryClient } from "@tanstack/react-query";

const TARGETS: { key: string; label: string; hint: string; required?: boolean }[] = [
  { key: "input", label: "Question / input", hint: "What the user asked", required: true },
  { key: "expected_output", label: "Reference answer", hint: "Enables correctness & context metrics" },
  { key: "response", label: "Recorded response", hint: "Pre-collected model answer to score offline" },
  { key: "context", label: "Context", hint: "List, JSON array, or chunks separated by blank lines" },
  { key: "criteria", label: "Evaluation criteria", hint: "Per-case rubric (Custom criteria metric)" },
  { key: "tags", label: "Tags", hint: "Comma / semicolon separated" },
];

const STEPS = ["File", "Map columns", "Review & import"] as const;

/**
 * Upload CSV/JSON/JSONL → column mapping → validation report (row numbers + reasons) → import.
 * With `dataset` set, the import becomes that dataset's next version.
 */
export function UploadWizard({
  pid,
  open,
  onOpenChange,
  dataset,
  onImported,
}: {
  pid: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  dataset?: Dataset;
  onImported?: (out: UploadOut) => void;
}) {
  const qc = useQueryClient();
  const [step, setStep] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [dragging, setDragging] = useState(false);
  const [dry, setDry] = useState<UploadOut | null>(null);
  const [mapping, setMapping] = useState<Record<string, string | null>>({});
  const [meta, setMeta] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [skipInvalid, setSkipInvalid] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const reset = () => {
    setStep(0);
    setFile(null);
    setName("");
    setDry(null);
    setMapping({});
    setMeta([]);
    setError(null);
    setSkipInvalid(false);
  };

  const analyse = useCallback(
    async (f: File, m?: Record<string, string | null>, md?: string[]) => {
      setBusy(true);
      setError(null);
      try {
        const out = await uploadDataset({ file: f, projectId: pid, datasetId: dataset?.id, mapping: m, metadataColumns: md, dryRun: true });
        setDry(out);
        if (!m) setMapping(out.mapping_used);
        return out;
      } catch (e) {
        setError(errorMessage(e));
        return null;
      } finally {
        setBusy(false);
      }
    },
    [pid, dataset?.id],
  );

  const pick = async (f: File) => {
    setFile(f);
    if (!name) setName(f.name.replace(/\.[^.]+$/, ""));
    const out = await analyse(f);
    if (out) setStep(1);
  };

  // re-validate when the mapping changes (debounced)
  useEffect(() => {
    if (!file || step === 0) return;
    const t = setTimeout(() => analyse(file, mapping, meta), 400);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(mapping), JSON.stringify(meta)]);

  const used = new Set(Object.values(mapping).filter(Boolean) as string[]);
  const unmapped = (dry?.columns ?? []).filter((c) => !used.has(c));
  const report = dry?.report;
  const canImport = !!report && report.valid_rows > 0 && dry!.mapping_problems.length === 0 && (report.invalid_rows === 0 || skipInvalid);

  const doImport = async () => {
    if (!file) return;
    setBusy(true);
    try {
      const out = await uploadDataset({
        file, projectId: pid, datasetId: dataset?.id, name: dataset ? undefined : name, mapping,
        metadataColumns: meta, dryRun: false, skipInvalid,
      });
      qc.invalidateQueries({ queryKey: ["datasets"] });
      qc.invalidateQueries({ queryKey: ["versions"] });
      qc.invalidateQueries({ queryKey: ["dataset"] });
      qc.invalidateQueries({ queryKey: ["cases"] });
      toast.success(`Imported ${out.version?.row_count} case(s) as ${out.dataset?.name} v${out.version?.version_number}`);
      onImported?.(out);
      onOpenChange(false);
      reset();
    } catch (e) {
      setError(e instanceof ApiError ? errorMessage(e) : "Import failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => (onOpenChange(o), o || reset())}>
      <DialogContent
        side="right"
        title={dataset ? `Upload a new version of ${dataset.name}` : "Upload dataset"}
        description={dataset ? `Creates v${(dataset.current_version?.version_number ?? 0) + 1}; earlier versions stay unchanged.` : "CSV, TSV, JSON or JSONL, up to 10 MB and 5,000 rows."}
      >
        <ol className="mb-5 flex items-center gap-2 text-xs" aria-label="Upload steps">
          {STEPS.map((s, i) => (
            <li key={s} className="flex items-center gap-2" aria-current={i === step ? "step" : undefined}>
              <span className={cn("flex size-5 items-center justify-center rounded-full border text-[11px]", i <= step ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground")}>
                {i + 1}
              </span>
              <span className={i === step ? "font-medium" : "text-muted-foreground"}>{s}</span>
              {i < STEPS.length - 1 && <span className="mx-1 h-px w-6 bg-border" />}
            </li>
          ))}
        </ol>

        {error && (
          <p role="alert" className="mb-4 rounded-md bg-fail-bg px-3 py-2 text-sm text-fail">
            {error}
          </p>
        )}

        {step === 0 && (
          <div className="space-y-4">
            {!dataset && (
              <Field label="Dataset name" htmlFor="up-name" hint="Defaults to the file name">
                <Input id="up-name" value={name} onChange={(e) => setName(e.target.value)} />
              </Field>
            )}
            <div
              role="button"
              tabIndex={0}
              aria-label="Choose a file or drop it here"
              onClick={() => inputRef.current?.click()}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && inputRef.current?.click()}
              onDragOver={(e) => (e.preventDefault(), setDragging(true))}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragging(false);
                const f = e.dataTransfer.files?.[0];
                if (f) pick(f);
              }}
              className={cn(
                "flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-14 text-center transition-colors",
                dragging ? "border-primary bg-primary/5" : "hover:border-primary/50",
              )}
            >
              <UploadCloud className="size-8 text-muted-foreground" aria-hidden />
              <p className="font-medium">{busy ? "Reading file…" : "Drop a file here or click to choose"}</p>
              <p className="text-xs text-muted-foreground">Columns like question, reference, answer, context and tags are mapped automatically.</p>
              <input
                ref={inputRef}
                type="file"
                accept=".csv,.tsv,.json,.jsonl,.ndjson"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) pick(f);
                  e.target.value = "";
                }}
              />
            </div>
          </div>
        )}

        {step >= 1 && dry && (
          <div className="space-y-5">
            <div className="flex items-center gap-2 text-sm">
              <FileUp className="size-4 text-muted-foreground" aria-hidden />
              <span className="font-medium">{file?.name}</span>
              <span className="text-muted-foreground">· {dry.columns.length} columns · {report?.total_rows ?? "?"} rows</span>
              <Button variant="link" size="sm" onClick={reset}>
                Choose another file
              </Button>
            </div>

            {step === 1 && (
              <>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-xs text-muted-foreground">
                      <th className="py-2 pr-3 font-medium">Field</th>
                      <th className="py-2 font-medium">Column in your file</th>
                    </tr>
                  </thead>
                  <tbody>
                    {TARGETS.map((t) => (
                      <tr key={t.key} className="border-b last:border-0">
                        <td className="py-2 pr-3 align-top">
                          <div className="font-medium">
                            {t.label} {t.required && <span className="text-destructive">*</span>}
                          </div>
                          <div className="text-xs text-muted-foreground">{t.hint}</div>
                        </td>
                        <td className="py-2">
                          <NativeSelect
                            aria-label={`Column for ${t.label}`}
                            value={mapping[t.key] ?? ""}
                            onChange={(e) => {
                              const v = e.target.value || null;
                              setMapping({ ...mapping, [t.key]: v });
                              if (v) setMeta(meta.filter((c) => c !== v));
                            }}
                          >
                            <option value="">— not mapped —</option>
                            {dry.columns.map((c) => (
                              <option key={c} value={c} disabled={used.has(c) && mapping[t.key] !== c}>
                                {c}
                              </option>
                            ))}
                          </NativeSelect>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {unmapped.length > 0 && (
                  <fieldset className="space-y-2">
                    <legend className="text-sm font-medium">Keep other columns as case metadata</legend>
                    <div className="flex flex-wrap gap-3">
                      {unmapped.map((c) => (
                        <label key={c} className="flex items-center gap-2 text-sm">
                          <Checkbox
                            checked={meta.includes(c)}
                            onCheckedChange={(v) => setMeta(v === true ? [...meta, c] : meta.filter((x) => x !== c))}
                          />
                          {c}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                )}
                {dry.mapping_problems.length > 0 && (
                  <ul role="alert" className="space-y-1 rounded-md bg-fail-bg px-3 py-2 text-sm text-fail">
                    {dry.mapping_problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                )}
                <div className="flex justify-end gap-2">
                  <Button disabled={!report || dry.mapping_problems.length > 0} loading={busy} onClick={() => setStep(2)}>
                    Review validation report
                  </Button>
                </div>
              </>
            )}

            {step === 2 && report && (
              <>
                <div className="grid grid-cols-3 gap-3">
                  <div className="rounded-md border p-3">
                    <div className="text-xs text-muted-foreground">Valid rows</div>
                    <div className="tabular flex items-center gap-1.5 text-xl font-semibold text-pass">
                      <CheckCircle2 className="size-5" aria-hidden /> {report.valid_rows}
                    </div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-xs text-muted-foreground">Rows with errors</div>
                    <div className={cn("tabular flex items-center gap-1.5 text-xl font-semibold", report.invalid_rows ? "text-fail" : "")}>
                      <XCircle className="size-5" aria-hidden /> {report.invalid_rows}
                    </div>
                  </div>
                  <div className="rounded-md border p-3">
                    <div className="text-xs text-muted-foreground">Warnings</div>
                    <div className={cn("tabular flex items-center gap-1.5 text-xl font-semibold", report.warnings.length ? "text-warn" : "")}>
                      <AlertTriangle className="size-5" aria-hidden /> {report.warnings.length}
                    </div>
                  </div>
                </div>
                <p className="text-sm text-muted-foreground">
                  Fields found: {report.fields_present.length ? report.fields_present.join(", ") : "question only"}.
                </p>
                {[...report.errors.map((e) => ({ ...e, kind: "error" as const })), ...report.warnings.map((w) => ({ ...w, kind: "warning" as const }))].length > 0 && (
                  <div className="max-h-56 overflow-y-auto rounded-md border">
                    <table className="w-full text-sm">
                      <thead className="sticky top-0 bg-muted">
                        <tr className="text-left text-xs text-muted-foreground">
                          <th className="px-3 py-1.5 font-medium">Row</th>
                          <th className="px-3 py-1.5 font-medium">Column</th>
                          <th className="px-3 py-1.5 font-medium">Issue</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.errors.map((e, i) => (
                          <tr key={`e${i}`} className="border-t">
                            <td className="tabular px-3 py-1.5">{e.row}</td>
                            <td className="px-3 py-1.5 font-mono text-xs">{e.column ?? "—"}</td>
                            <td className="px-3 py-1.5 text-fail">
                              <XCircle className="mr-1 inline size-3.5" aria-label="error" />
                              {e.message}
                            </td>
                          </tr>
                        ))}
                        {report.warnings.map((w, i) => (
                          <tr key={`w${i}`} className="border-t">
                            <td className="tabular px-3 py-1.5">{w.row}</td>
                            <td className="px-3 py-1.5 font-mono text-xs">{w.column ?? "—"}</td>
                            <td className="px-3 py-1.5 text-warn">
                              <AlertTriangle className="mr-1 inline size-3.5" aria-label="warning" />
                              {w.message}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {report.preview.length > 0 && (
                  <div className="space-y-1">
                    <h4 className="text-sm font-medium">Preview (first {report.preview.length} valid rows)</h4>
                    <div className="max-h-56 overflow-auto rounded-md border">
                      <table className="w-full text-xs">
                        <thead className="sticky top-0 bg-muted text-left text-muted-foreground">
                          <tr>
                            <th className="px-2 py-1.5 font-medium">Row</th>
                            <th className="px-2 py-1.5 font-medium">Input</th>
                            <th className="px-2 py-1.5 font-medium">Reference</th>
                            <th className="px-2 py-1.5 font-medium">Response</th>
                            <th className="px-2 py-1.5 font-medium">Context</th>
                            <th className="px-2 py-1.5 font-medium">Tags</th>
                          </tr>
                        </thead>
                        <tbody>
                          {report.preview.map((p) => {
                            const row = p as { row: number; input: string; expected_output?: string; response?: string; context?: string[]; tags?: string[] };
                            return (
                              <tr key={row.row} className="border-t align-top">
                                <td className="tabular px-2 py-1.5">{row.row}</td>
                                <td className="max-w-48 truncate px-2 py-1.5" title={row.input}>{row.input}</td>
                                <td className="max-w-40 truncate px-2 py-1.5">{row.expected_output ?? "—"}</td>
                                <td className="max-w-40 truncate px-2 py-1.5">{row.response ?? "—"}</td>
                                <td className="px-2 py-1.5">{row.context?.length ? `${row.context.length} chunk(s)` : "—"}</td>
                                <td className="px-2 py-1.5">{row.tags?.join(", ") || "—"}</td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                {report.invalid_rows > 0 && (
                  <label className="flex items-center gap-2 text-sm">
                    <Checkbox checked={skipInvalid} onCheckedChange={(v) => setSkipInvalid(v === true)} />
                    Import the {report.valid_rows} valid row(s) and skip {report.invalid_rows} row(s) with errors
                  </label>
                )}
                <div className="flex justify-between gap-2">
                  <Button variant="outline" onClick={() => setStep(1)}>
                    Back to mapping
                  </Button>
                  <Button disabled={!canImport} loading={busy} onClick={doImport}>
                    Import {report.invalid_rows && skipInvalid ? report.valid_rows : report.valid_rows} case(s)
                  </Button>
                </div>
              </>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
