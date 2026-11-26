import { zodResolver } from "@hookform/resolvers/zod";
import { CheckCircle2, KeyRound, Plug, Plus, Trash2, XCircle } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { ConfirmButton, EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/overlay";
import { Badge, Card, Field, Input } from "@/components/ui/primitives";
import { useDeleteProvider, useProject, useProviders, useSaveProvider, useTestProvider } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { errorMessage } from "@/services/api";
import type { Provider } from "@/types";

const keySchema = z.string().trim().min(8, "Paste the full API key").max(500);
const newSchema = z.object({ name: z.string().trim().min(1, "Name is required").max(200), api_key: keySchema });

function ProviderDialog({
  pid,
  open,
  onOpenChange,
  replace,
}: {
  pid: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  replace?: Provider;
}) {
  const save = useSaveProvider(pid);
  const test = useTestProvider(pid);
  const form = useForm<z.infer<typeof newSchema>>({
    resolver: zodResolver(replace ? newSchema.pick({ api_key: true }).extend({ name: z.string() }) : newSchema),
    mode: "onChange",
    defaultValues: { name: replace?.name ?? "OpenRouter", api_key: "" },
  });
  const { errors, isValid } = form.formState;
  return (
    <Dialog open={open} onOpenChange={(o) => (onOpenChange(o), o || form.reset())}>
      <DialogContent
        title={replace ? `Replace key for ${replace.name}` : "Add OpenRouter provider"}
        description="The key is encrypted at rest and never shown again. Only the last four characters are displayed."
      >
        <form
          noValidate
          autoComplete="off"
          className="space-y-4"
          onSubmit={form.handleSubmit(async (v) => {
            try {
              const p = await save.mutateAsync(replace ? { id: replace.id, api_key: v.api_key } : v);
              form.reset();
              onOpenChange(false);
              const t = await test.mutateAsync(p.id);
              if (t.ok) toast.success(`Key saved. ${t.message}`);
              else toast.error(`Key saved, but the connection test failed: ${t.message}`);
            } catch (e) {
              toast.error(errorMessage(e));
            }
          })}
        >
          {!replace && (
            <Field label="Name" htmlFor="prov-name" error={errors.name?.message}>
              <Input id="prov-name" aria-invalid={!!errors.name} {...form.register("name")} />
            </Field>
          )}
          <Field
            label="OpenRouter API key"
            htmlFor="prov-key"
            error={errors.api_key?.message}
            hint="Create one at openrouter.ai/keys. Used for both judge calls and generated answers."
          >
            <Input
              id="prov-key"
              type="password"
              placeholder="sk-or-v1-…"
              autoComplete="new-password"
              aria-invalid={!!errors.api_key}
              {...form.register("api_key")}
            />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!isValid} loading={save.isPending || test.isPending}>
              {replace ? "Replace key" : "Save and test"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ProvidersPage() {
  const pid = Number(useParams().projectId);
  const project = useProject(pid);
  const providers = useProviders(pid);
  const test = useTestProvider(pid);
  const del = useDeleteProvider(pid);
  const [open, setOpen] = useState(false);
  const [replace, setReplace] = useState<Provider | undefined>();
  const isOwner = project.data?.role === "owner";

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Settings" }, { label: "Providers & API Keys" }]}
        title="Providers & API Keys"
        description="Model providers used to generate answers and to run judge models. Keys are write-only."
        actions={
          isOwner && (
            <Button onClick={() => (setReplace(undefined), setOpen(true))}>
              <Plus /> Add provider
            </Button>
          )
        }
      />
      <div className="space-y-4 p-6">
        {!isOwner && project.data && (
          <p className="text-sm text-muted-foreground">Only project owners can add or change provider keys.</p>
        )}
        {providers.isLoading && (
          <Card>
            <TableSkeleton rows={2} cols={4} />
          </Card>
        )}
        {providers.isError && <ErrorState error={providers.error} onRetry={() => providers.refetch()} />}
        {providers.data?.length === 0 && (
          <EmptyState
            icon={KeyRound}
            title="No providers yet"
            description="Add your OpenRouter key to generate answers and run Ragas/DeepEval judges."
            action={
              isOwner && (
                <Button onClick={() => setOpen(true)}>
                  <Plus /> Add OpenRouter key
                </Button>
              )
            }
          />
        )}
        {providers.data?.map((p) => (
          <Card key={p.id} className="flex items-center gap-4 p-4">
            <div className="flex size-10 items-center justify-center rounded-md bg-muted">
              <Plug className="size-5 text-muted-foreground" aria-hidden />
            </div>
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">{p.name}</span>
                <Badge variant="secondary">{p.type}</Badge>
              </div>
              <div className="flex items-center gap-3 text-sm text-muted-foreground">
                <span className="font-mono">{p.key_hint}</span>
                {p.status === "ok" && (
                  <span className="inline-flex items-center gap-1 text-pass">
                    <CheckCircle2 className="size-4" aria-hidden /> {p.status_message}
                  </span>
                )}
                {p.status === "error" && (
                  <span className="inline-flex items-center gap-1 text-fail">
                    <XCircle className="size-4" aria-hidden /> {p.status_message}
                  </span>
                )}
                {p.status === "untested" && <span>Not tested yet</span>}
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              loading={test.isPending && test.variables === p.id}
              onClick={async () => {
                try {
                  const t = await test.mutateAsync(p.id);
                  if (t.ok) toast.success(t.message);
                  else toast.error(t.message);
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              }}
            >
              Test connection
            </Button>
            {isOwner && (
              <>
                <Button variant="outline" size="sm" onClick={() => (setReplace(p), setOpen(true))}>
                  Replace key
                </Button>
                <ConfirmButton
                  variant="ghost"
                  size="icon"
                  title={`Delete ${p.name}?`}
                  confirmLabel="Delete provider"
                  consequences={
                    <>
                      The encrypted key is destroyed. Judge configurations that use this provider are deleted too,
                      and queued runs that depend on it will error. Past results are kept.
                    </>
                  }
                  onConfirm={async () => {
                    try {
                      await del.mutateAsync(p.id);
                      toast.success("Provider deleted");
                    } catch (e) {
                      toast.error(errorMessage(e));
                    }
                  }}
                >
                  <Trash2 aria-label={`Delete ${p.name}`} />
                </ConfirmButton>
              </>
            )}
          </Card>
        ))}
      </div>
      <ProviderDialog pid={pid} open={open} onOpenChange={setOpen} replace={replace} />
    </>
  );
}
