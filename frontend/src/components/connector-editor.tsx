import { CheckCircle2, ChevronDown, ChevronRight, PlugZap, Save, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Field, Input, NativeSelect, Textarea } from "@/components/ui/primitives";
import { testConnector, useConnectorMutations } from "@/hooks/queries";
import { errorMessage } from "@/services/api";
import type { ConnectorTestOut, RagConnector } from "@/types";

export type ConnectorDraft = {
  name: string;
  endpoint_url: string;
  method: "POST" | "GET";
  auth_header_name: string;
  auth_value: string;
  request_template: string;
  answer_path: string;
  contexts_path: string;
  context_text_path: string;
  timeout_s: number;
};

const EMPTY: ConnectorDraft = {
  name: "My RAG API",
  endpoint_url: "https://",
  method: "POST",
  auth_header_name: "Authorization",
  auth_value: "",
  request_template: '{\n  "question": "{{question}}"\n}',
  answer_path: "answer",
  contexts_path: "contexts",
  context_text_path: "",
  timeout_s: 30,
};

const fromConnector = (c: RagConnector): ConnectorDraft => {
  const m = c.response_mapping as Record<string, string>;
  return {
    name: c.name, endpoint_url: c.endpoint_url, method: c.method as "POST" | "GET", auth_header_name: c.auth_header_name,
    auth_value: "", request_template: c.request_template, answer_path: m.answer_path ?? "answer",
    contexts_path: m.contexts_path ?? "", context_text_path: m.context_text_path ?? "", timeout_s: c.timeout_s,
  };
};

function localProblems(d: ConnectorDraft): string[] {
  const p: string[] = [];
  if (!d.name.trim()) p.push("Name is required");
  if (!/^https?:\/\/.+/.test(d.endpoint_url.trim())) p.push("Endpoint must start with http:// or https://");
  try {
    JSON.parse(d.request_template);
    if (!d.request_template.includes("{{question}}")) p.push("Request template must contain {{question}}");
  } catch {
    p.push("Request template is not valid JSON");
  }
  if (!d.answer_path.trim()) p.push("Answer path is required");
  if (d.auth_header_name && !/^[A-Za-z0-9\-_]+$/.test(d.auth_header_name)) p.push("Header name may only contain letters, digits, - and _");
  return p;
}

const body = (d: ConnectorDraft) => ({
  name: d.name.trim(),
  endpoint_url: d.endpoint_url.trim(),
  method: d.method,
  auth_header_name: d.auth_header_name.trim(),
  auth_value: d.auth_value || null,
  request_template: d.request_template,
  response_mapping: { answer_path: d.answer_path.trim(), contexts_path: d.contexts_path.trim(), context_text_path: d.context_text_path.trim() },
  timeout_s: Number(d.timeout_s) || 30,
});

/** Create/edit a RAG connector and test it against the live endpoint with a sample question. */
export function ConnectorEditor({
  pid,
  connector,
  sampleQuestion,
  onSaved,
}: {
  pid: number;
  connector?: RagConnector;
  sampleQuestion: string;
  onSaved: (c: RagConnector) => void;
}) {
  const m = useConnectorMutations(pid);
  const [d, setD] = useState<ConnectorDraft>(connector ? fromConnector(connector) : EMPTY);
  const [question, setQuestion] = useState(sampleQuestion);
  const [result, setResult] = useState<ConnectorTestOut | null>(null);
  const [testing, setTesting] = useState(false);
  const [showRaw, setShowRaw] = useState(false);
  useEffect(() => setD(connector ? fromConnector(connector) : EMPTY), [connector]);
  useEffect(() => setQuestion((q) => q || sampleQuestion), [sampleQuestion]);
  const set = (patch: Partial<ConnectorDraft>) => (setD({ ...d, ...patch }), setResult(null));
  const problems = localProblems(d);

  const test = async () => {
    setTesting(true);
    try {
      setResult(await testConnector({ ...body(d), project_id: pid, connector_id: connector?.id ?? null, question }));
    } catch (e) {
      setResult({ ok: false, error: errorMessage(e), warnings: [], raw_preview: "" });
    } finally {
      setTesting(false);
    }
  };

  const save = async () => {
    try {
      const saved = await m.save.mutateAsync({ ...(connector ? { id: connector.id } : {}), ...body(d) });
      toast.success(`Connector "${saved.name}" saved`);
      onSaved(saved);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };

  return (
    <div className="grid grid-cols-[1fr_1fr] gap-6">
      <div className="space-y-4">
        <Field label="Name" htmlFor="cn-name">
          <Input id="cn-name" value={d.name} onChange={(e) => set({ name: e.target.value })} />
        </Field>
        <div className="grid grid-cols-[110px_1fr] gap-3">
          <Field label="Method" htmlFor="cn-method">
            <NativeSelect id="cn-method" value={d.method} onChange={(e) => set({ method: e.target.value as "POST" | "GET" })}>
              <option>POST</option>
              <option>GET</option>
            </NativeSelect>
          </Field>
          <Field label="Endpoint URL" htmlFor="cn-url" hint="Must be publicly reachable; private and internal addresses are blocked.">
            <Input id="cn-url" value={d.endpoint_url} onChange={(e) => set({ endpoint_url: e.target.value })} placeholder="https://rag.example.com/ask" />
          </Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Auth header (optional)" htmlFor="cn-hdr">
            <Input id="cn-hdr" value={d.auth_header_name} onChange={(e) => set({ auth_header_name: e.target.value })} placeholder="Authorization" />
          </Field>
          <Field
            label="Header value"
            htmlFor="cn-auth"
            hint={connector?.auth_hint ? `Stored: ${connector.auth_hint}. Leave blank to keep it.` : "Encrypted at rest; never shown again."}
          >
            <Input id="cn-auth" type="password" autoComplete="new-password" value={d.auth_value} onChange={(e) => set({ auth_value: e.target.value })} placeholder="Bearer …" />
          </Field>
        </div>
        <Field
          label={d.method === "GET" ? "Query parameters (JSON)" : "Request body template (JSON)"}
          htmlFor="cn-tpl"
          hint={'"{{question}}" is replaced by each test question (JSON-escaped).'}
        >
          <Textarea id="cn-tpl" rows={5} className="font-mono text-xs" value={d.request_template} onChange={(e) => set({ request_template: e.target.value })} />
        </Field>
        <fieldset className="space-y-3 rounded-lg border p-3">
          <legend className="px-1 text-sm font-medium">Response mapping (JSON paths)</legend>
          <Field label="Answer path" htmlFor="cn-ans" hint='e.g. answer · data.output · choices[0].message.content'>
            <Input id="cn-ans" className="font-mono text-xs" value={d.answer_path} onChange={(e) => set({ answer_path: e.target.value })} />
          </Field>
          <Field label="Retrieved contexts path" htmlFor="cn-ctx" hint="e.g. contexts · sources[*] · result.documents[*]. Leave empty if the API does not return them (retrieval metrics then show N/A).">
            <Input id="cn-ctx" className="font-mono text-xs" value={d.contexts_path} onChange={(e) => set({ contexts_path: e.target.value })} />
          </Field>
          <Field label="Text inside each context (optional)" htmlFor="cn-ctxt" hint="e.g. text · page_content. Without it, text/content/page_content are tried.">
            <Input id="cn-ctxt" className="font-mono text-xs" value={d.context_text_path} onChange={(e) => set({ context_text_path: e.target.value })} />
          </Field>
        </fieldset>
        <Field label="Timeout (seconds)" htmlFor="cn-to" className="w-40">
          <Input id="cn-to" type="number" min={1} max={300} value={d.timeout_s} onChange={(e) => set({ timeout_s: Number(e.target.value) })} />
        </Field>
        {problems.length > 0 && (
          <ul role="alert" className="space-y-0.5 rounded-md bg-fail-bg px-3 py-2 text-sm text-fail">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-3">
        <div className="rounded-lg border p-3">
          <Field label="Sample question" htmlFor="cn-q" hint="Taken from the first case of your dataset.">
            <Textarea id="cn-q" rows={2} value={question} onChange={(e) => setQuestion(e.target.value)} />
          </Field>
          <div className="mt-3 flex gap-2">
            <Button variant="outline" disabled={problems.length > 0 || !question.trim()} loading={testing} onClick={test}>
              <PlugZap /> Test connection
            </Button>
            <Button disabled={problems.length > 0} loading={m.save.isPending} onClick={save}>
              <Save /> {connector ? "Save changes" : "Save connector"}
            </Button>
          </div>
        </div>
        {!result && (
          <p className="rounded-lg border border-dashed p-6 text-center text-sm text-muted-foreground">
            Test the connection to preview the parsed answer and retrieved contexts before running.
          </p>
        )}
        {result && !result.ok && (
          <div role="alert" className="flex gap-2 rounded-lg bg-fail-bg p-3 text-sm text-fail">
            <XCircle className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span className="break-words">{result.error}</span>
          </div>
        )}
        {result?.ok && (
          <div className="space-y-3 rounded-lg border p-3 text-sm">
            <p className="flex items-center gap-2 text-pass">
              <CheckCircle2 className="size-4" aria-hidden /> HTTP {result.status_code} in {result.latency_ms} ms
            </p>
            {result.warnings.map((w) => (
              <p key={w} className="rounded bg-warn-bg px-2 py-1 text-xs text-warn">
                {w}
              </p>
            ))}
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Parsed answer</h4>
              <p className="mt-1 whitespace-pre-wrap rounded bg-muted/50 p-2">{result.answer}</p>
            </div>
            <div>
              <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Retrieved contexts ({result.contexts?.length ?? "not mapped"})
              </h4>
              <ol className="mt-1 space-y-1">
                {result.contexts?.map((c, i) => (
                  <li key={i} className="rounded border p-2 text-xs">
                    <span className="mr-1 font-semibold text-muted-foreground">#{i + 1}</span>
                    {c}
                  </li>
                ))}
              </ol>
            </div>
            <button type="button" className="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground" onClick={() => setShowRaw(!showRaw)} aria-expanded={showRaw}>
              {showRaw ? <ChevronDown className="size-3" /> : <ChevronRight className="size-3" />} Raw response (first 2 KB)
            </button>
            {showRaw && <pre className="max-h-48 overflow-auto rounded bg-muted p-2 text-[11px]">{result.raw_preview}</pre>}
          </div>
        )}
      </div>
    </div>
  );
}
