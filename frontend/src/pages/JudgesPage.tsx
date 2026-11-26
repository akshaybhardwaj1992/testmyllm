import { zodResolver } from "@hookform/resolvers/zod";
import { Pencil, Plus, Sparkles, Star, Trash2 } from "lucide-react";
import { useState } from "react";
import { Controller, useForm } from "react-hook-form";

import { ModelPicker } from "@/components/model-picker";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { ConfirmButton, EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/overlay";
import { Badge, Card, Field, Input, NativeSelect, Switch } from "@/components/ui/primitives";
import { useCatalogModels, useDeleteJudge, useJudges, useProviders, useSaveJudge } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { errorMessage } from "@/services/api";
import type { JudgeConfig } from "@/types";

const schema = z.object({
  name: z.string().trim().min(1, "Name is required").max(200),
  provider_id: z.coerce.number().int().positive("Choose a provider"),
  model: z.string().trim().min(1, "Choose a judge model").max(200),
  temperature: z.coerce.number().min(0, "0 to 2").max(2, "0 to 2"),
  embedding_model: z.string().trim().min(1, "Required for Ragas answer relevancy").max(200),
  is_default: z.boolean(),
});
type Form = z.infer<typeof schema>;

export function ModelDatalist({ id }: { id: string }) {
  const models = useCatalogModels();
  return (
    <datalist id={id}>
      {models.data?.map((m) => (
        <option key={m.id} value={m.id}>
          {m.price_in_per_mtok != null ? `$${m.price_in_per_mtok.toFixed(2)} / $${m.price_out_per_mtok?.toFixed(2)} per Mtok` : ""}
        </option>
      ))}
    </datalist>
  );
}

function JudgeDialog({
  pid,
  open,
  onOpenChange,
  edit,
}: {
  pid: number;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  edit?: JudgeConfig;
}) {
  const providers = useProviders(pid);
  const save = useSaveJudge(pid);
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    mode: "onChange",
    values: {
      name: edit?.name ?? "Default judge",
      provider_id: edit?.provider_id ?? providers.data?.[0]?.id ?? 0,
      model: edit?.model ?? "openai/gpt-4o-mini",
      temperature: edit?.temperature ?? 0,
      embedding_model: edit?.embedding_model ?? "openai/text-embedding-3-small",
      is_default: edit?.is_default ?? false,
    },
  });
  const { errors, isValid, isDirty } = form.formState;

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && isDirty && !window.confirm("Discard unsaved changes?")) return;
        onOpenChange(o);
      }}
    >
      <DialogContent
        title={edit ? `Edit ${edit.name}` : "New judge configuration"}
        description="The judge model scores answers for both Ragas and DeepEval. Temperature defaults to 0; repeats still measure its variance."
      >
        <form
          noValidate
          className="space-y-4"
          onSubmit={form.handleSubmit(async (v) => {
            try {
              await save.mutateAsync(edit ? { id: edit.id, ...v } : v);
              toast.success(edit ? "Judge updated" : "Judge created");
              form.reset(v);
              onOpenChange(false);
            } catch (e) {
              toast.error(errorMessage(e));
            }
          })}
        >
          <Field label="Name" htmlFor="j-name" error={errors.name?.message}>
            <Input id="j-name" aria-invalid={!!errors.name} {...form.register("name")} />
          </Field>
          {!edit && (
            <Field label="Provider" htmlFor="j-prov" error={errors.provider_id?.message}>
              <NativeSelect id="j-prov" {...form.register("provider_id")}>
                {providers.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.key_hint})
                  </option>
                ))}
              </NativeSelect>
            </Field>
          )}
          <Field
            label="Judge model"
            htmlFor="j-model"
            error={errors.model?.message}
            hint="OpenRouter model id. Use a model from a different family than the models you test to limit self-preference bias."
          >
            <Controller control={form.control} name="model" render={({ field }) => <ModelPicker id="j-model" value={field.value ?? ""} onChange={field.onChange} />} />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label="Temperature" htmlFor="j-temp" error={errors.temperature?.message}>
              <Input id="j-temp" type="number" step="0.1" min={0} max={2} {...form.register("temperature")} />
            </Field>
            <Field label="Embedding model" htmlFor="j-emb" error={errors.embedding_model?.message} hint="Used by Ragas answer relevancy">
              <Controller
                control={form.control}
                name="embedding_model"
                render={({ field }) => <ModelPicker id="j-emb" value={field.value ?? ""} onChange={field.onChange} needs="embedding" />}
              />
            </Field>
          </div>
          <div className="flex items-center gap-3">
            <Controller
              control={form.control}
              name="is_default"
              render={({ field }) => (
                <Switch id="j-default" checked={field.value} onCheckedChange={field.onChange} />
              )}
            />
            <label htmlFor="j-default" className="text-sm">
              Use as the project's default judge
            </label>
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!isValid} loading={save.isPending}>
              {edit ? "Save changes" : "Create judge"}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function JudgesPage() {
  const pid = Number(useParams().projectId);
  const judges = useJudges(pid);
  const providers = useProviders(pid);
  const save = useSaveJudge(pid);
  const del = useDeleteJudge(pid);
  const [open, setOpen] = useState(false);
  const [edit, setEdit] = useState<JudgeConfig | undefined>();
  const noProvider = providers.data?.length === 0;

  return (
    <>
      <PageHeader
        crumbs={[{ label: "Settings" }, { label: "Evaluation Models" }]}
        title="Evaluation Models"
        description="Judge and embedding models. Every metric result records which judge produced it."
        actions={
          <Button disabled={noProvider} onClick={() => (setEdit(undefined), setOpen(true))}>
            <Plus /> New judge
          </Button>
        }
      />
      <div className="space-y-4 p-6">
        {judges.isLoading && (
          <Card>
            <TableSkeleton rows={2} cols={4} />
          </Card>
        )}
        {judges.isError && <ErrorState error={judges.error} onRetry={() => judges.refetch()} />}
        {judges.data?.length === 0 && (
          <EmptyState
            icon={Sparkles}
            title="No judge configured"
            description={
              noProvider ? (
                <>
                  Add a provider first in{" "}
                  <Link className="text-primary underline" to={`/p/${pid}/settings/providers`}>
                    Providers & API Keys
                  </Link>
                  .
                </>
              ) : (
                "A judge model scores answers. gpt-4o-mini is a cheap, reasonable default."
              )
            }
            action={
              !noProvider && (
                <Button onClick={() => setOpen(true)}>
                  <Plus /> New judge
                </Button>
              )
            }
          />
        )}
        {judges.data?.map((j) => (
          <Card key={j.id} className="flex items-center gap-4 p-4">
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">{j.name}</span>
                {j.is_default && (
                  <Badge>
                    <Star className="size-3" aria-hidden /> Default
                  </Badge>
                )}
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted-foreground">
                <span>
                  Model <span className="font-mono text-foreground">{j.model}</span>
                </span>
                <span>Temperature {j.temperature}</span>
                <span>
                  Embeddings <span className="font-mono">{j.embedding_model}</span>
                </span>
              </div>
            </div>
            {!j.is_default && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => save.mutate({ id: j.id, is_default: true }, { onError: (e) => toast.error(errorMessage(e)) })}
              >
                Set as default
              </Button>
            )}
            <Button variant="ghost" size="icon" aria-label={`Edit ${j.name}`} onClick={() => (setEdit(j), setOpen(true))}>
              <Pencil />
            </Button>
            <ConfirmButton
              variant="ghost"
              size="icon"
              title={`Delete ${j.name}?`}
              confirmLabel="Delete judge"
              consequences="Past results keep the judge model name they were scored with. Queued runs using this judge keep their snapshot."
              onConfirm={async () => {
                try {
                  await del.mutateAsync(j.id);
                  toast.success("Judge deleted");
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              }}
            >
              <Trash2 aria-label={`Delete ${j.name}`} />
            </ConfirmButton>
          </Card>
        ))}
      </div>
      <JudgeDialog pid={pid} open={open} onOpenChange={setOpen} edit={edit} />
    </>
  );
}
