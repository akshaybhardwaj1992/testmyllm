import { Check, Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router-dom";
import { toast } from "sonner";

import { ConfirmButton, ErrorState, TableSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input, NativeSelect } from "@/components/ui/primitives";
import { useApiTokens, useBaselines, useGates, useProject, useSuites, useTokenMutations } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { fmtDate } from "@/lib/utils";
import { errorMessage } from "@/services/api";

function CopyBlock({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="relative">
      <pre className="overflow-x-auto rounded-md bg-muted p-3 pr-12 font-mono text-xs leading-relaxed">{text}</pre>
      <Button
        variant="ghost"
        size="icon"
        className="absolute right-1 top-1"
        aria-label={`Copy ${label}`}
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          } catch {
            toast.error("Copy failed: select the text and copy it manually");
          }
        }}
      >
        {copied ? <Check /> : <Copy />}
      </Button>
    </div>
  );
}

export function IntegrationsPage() {
  const pid = Number(useParams().projectId);
  const project = useProject(pid);
  const isOwner = project.data?.role === "owner";
  const tokens = useApiTokens(pid, isOwner);
  const tm = useTokenMutations(pid);
  const suites = useSuites(pid);
  const baselines = useBaselines(pid);
  const gates = useGates(pid);
  const [name, setName] = useState("GitHub Actions");
  const [role, setRole] = useState<"editor" | "viewer">("editor");
  const [fresh, setFresh] = useState<string | null>(null);

  const suite = suites.data?.[0]?.id ?? "<suite-id>";
  const baseline = baselines.data?.[0]?.id ?? "<baseline-id>";
  const gate = gates.data?.[0]?.id ?? "<gate-id>";
  const origin = window.location.origin;

  const cli = `pip install ./cli
export TESTMYLLM_URL=${origin}
export TESTMYLLM_TOKEN=tml_...   # project API token

testmyllm run --suite ${suite} --config testmyllm/run-config.json \\
  --baseline ${baseline} --gate ${gate} --fail-on-gate
# exit 0 = gate passed · 1 = gate failed · 2 = error`;

  const action = `- uses: ./.github/actions/testmyllm
  with:
    url: \${{ secrets.TESTMYLLM_URL }}
    token: \${{ secrets.TESTMYLLM_TOKEN }}
    suite: "${suite}"
    config: testmyllm/run-config.json
    baseline: "${baseline}"
    gate: "${gate}"
    fail-on-gate: "true"`;

  const mcpCli = `pip install ./mcp_server
claude mcp add testmyllm --env TESTMYLLM_URL=${origin} --env TESTMYLLM_TOKEN=tml_... -- testmyllm-mcp`;
  const mcpDesktop = JSON.stringify(
    { mcpServers: { testmyllm: { command: "testmyllm-mcp", env: { TESTMYLLM_URL: origin, TESTMYLLM_TOKEN: "tml_..." } } } },
    null,
    2,
  );

  return (
    <>
      <PageHeader crumbs={[{ label: "Settings" }, { label: "Integrations" }]} title="Integrations" description="Run evaluations and quality gates from CI with the testmyllm CLI and GitHub Action." />
      <div className="max-w-4xl space-y-6 p-6">
        <Card>
          <CardHeader>
            <CardTitle>API tokens</CardTitle>
            <CardDescription>
              Project-scoped tokens for the CLI, CI and the MCP server. A token can only see this project and is capped at its role; editor tokens can
              start runs and evaluate gates but cannot manage provider keys or members.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {!isOwner ? (
              <p className="text-sm text-muted-foreground">Only project owners can create or view API tokens.</p>
            ) : (
              <>
                {fresh && (
                  <div role="status" className="space-y-2 rounded-md border border-pass/40 bg-pass-bg p-3">
                    <p className="text-sm font-medium text-pass">Copy this token now. It will not be shown again.</p>
                    <CopyBlock text={fresh} label="token" />
                    <Button variant="outline" size="sm" onClick={() => setFresh(null)}>
                      I have stored it
                    </Button>
                  </div>
                )}
                <div className="grid grid-cols-[1fr_160px_auto] items-end gap-3">
                  <Field label="Token name" htmlFor="tk-name">
                    <Input id="tk-name" value={name} onChange={(e) => setName(e.target.value)} />
                  </Field>
                  <Field label="Role" htmlFor="tk-role">
                    <NativeSelect id="tk-role" value={role} onChange={(e) => setRole(e.target.value as "editor" | "viewer")}>
                      <option value="editor">Editor (run + gate)</option>
                      <option value="viewer">Viewer (read only)</option>
                    </NativeSelect>
                  </Field>
                  <Button
                    disabled={!name.trim()}
                    loading={tm.create.isPending}
                    onClick={async () => {
                      try {
                        const t = await tm.create.mutateAsync({ name: name.trim(), role });
                        setFresh(t.token);
                      } catch (e) {
                        toast.error(errorMessage(e));
                      }
                    }}
                  >
                    <Plus /> Create token
                  </Button>
                </div>
                {tokens.isLoading && <TableSkeleton rows={2} cols={4} />}
                {tokens.isError && <ErrorState error={tokens.error} onRetry={() => tokens.refetch()} />}
                {!!tokens.data?.length && (
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-xs text-muted-foreground">
                        <th className="py-2 font-medium">Name</th>
                        <th className="py-2 font-medium">Token</th>
                        <th className="py-2 font-medium">Role</th>
                        <th className="py-2 font-medium">Created</th>
                        <th className="py-2 font-medium">Last used</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {tokens.data.map((t) => (
                        <tr key={t.id} className="border-b last:border-0">
                          <td className="py-2 font-medium">
                            <KeyRound className="mr-1.5 inline size-3.5 text-muted-foreground" aria-hidden />
                            {t.name}
                          </td>
                          <td className="py-2 font-mono text-xs">{t.prefix}…</td>
                          <td className="py-2">
                            <Badge variant="secondary">{t.role}</Badge>
                          </td>
                          <td className="py-2 text-muted-foreground">{fmtDate(t.created_at)}</td>
                          <td className="py-2 text-muted-foreground">{t.revoked_at ? <span className="text-fail">revoked</span> : fmtDate(t.last_used_at)}</td>
                          <td className="py-2 text-right">
                            {!t.revoked_at && (
                              <ConfirmButton
                                variant="ghost"
                                size="icon"
                                title={`Revoke “${t.name}”?`}
                                confirmLabel="Revoke token"
                                consequences="Anything using this token (CI jobs, scripts) stops working immediately."
                                onConfirm={async () => {
                                  try {
                                    await tm.revoke.mutateAsync(t.id);
                                    toast.success("Token revoked");
                                  } catch (e) {
                                    toast.error(errorMessage(e));
                                  }
                                }}
                              >
                                <Trash2 aria-label={`Revoke ${t.name}`} />
                              </ConfirmButton>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>testmyllm CLI</CardTitle>
            <CardDescription>Ids below are filled in from this project (first suite, baseline and gate). The config file holds the judge, metrics and model under test.</CardDescription>
          </CardHeader>
          <CardContent>
            <CopyBlock text={cli} label="CLI commands" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>GitHub Action</CardTitle>
            <CardDescription>
              Add <code>TESTMYLLM_URL</code> and <code>TESTMYLLM_TOKEN</code> as repository secrets. The job fails when the gate fails, and writes a gate summary to the
              run page. Full example: <code>.github/workflows/llm-regression.example.yml</code>.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <CopyBlock text={action} label="GitHub Action step" />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>MCP server</CardTitle>
            <CardDescription>
              Lets Claude (Claude Code, Claude Desktop or any MCP client) use TestMyLLM: <code>score_answers</code>, <code>run_evaluation</code>, <code>get_run</code>,{" "}
              <code>compare_runs</code> and <code>list_projects</code>. It uses these API tokens: an editor token can start runs, a viewer token is read-only. Tools that cost money
              refuse when the estimate exceeds <code>max_estimated_usd</code> (0.25 for scoring, 1.00 for batch runs by default).
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm font-medium">Claude Code</p>
            <CopyBlock text={mcpCli} label="Claude Code command" />
            <p className="text-sm font-medium">Claude Desktop (claude_desktop_config.json)</p>
            <CopyBlock text={mcpDesktop} label="Claude Desktop config" />
          </CardContent>
        </Card>
      </div>
    </>
  );
}
