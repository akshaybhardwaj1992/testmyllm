import { zodResolver } from "@hookform/resolvers/zod";
import { Info, Trash2 } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { ConfirmButton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/overlay";
import { Field, Input, Textarea } from "@/components/ui/primitives";
import { useCaseMutations } from "@/hooks/queries";
import { errorMessage } from "@/services/api";
import type { CaseEdit, CaseFull } from "@/types";

const schema = z.object({
  input: z.string().trim().min(1, "Input is required").max(20000),
  expected_output: z.string().max(20000),
  recorded_output: z.string().max(50000),
  context: z.string().max(200000),
  criteria: z.string().max(5000),
  tags: z.string().max(1000),
});
type Form = z.infer<typeof schema>;

const chunks = (s: string) => s.split(/\n\s*\n/).map((c) => c.trim()).filter(Boolean);

/**
 * Test case drawer. Saving never mutates a version: it creates v{n+1} (spec §10.2).
 * `caseItem` undefined = add a new case.
 */
export function CaseEditor({
  datasetId,
  versionId,
  nextVersion,
  caseItem,
  readOnly,
  open,
  onOpenChange,
  onSaved,
}: {
  datasetId: number;
  versionId: number;
  nextVersion: number;
  caseItem?: CaseFull;
  readOnly?: boolean;
  open: boolean;
  onOpenChange: (o: boolean) => void;
  onSaved: (edit: CaseEdit) => void;
}) {
  const m = useCaseMutations(datasetId);
  const form = useForm<Form>({
    resolver: zodResolver(schema),
    mode: "onChange",
    values: {
      input: caseItem?.input ?? "",
      expected_output: caseItem?.expected_output ?? "",
      recorded_output: caseItem?.recorded_output ?? "",
      context: (caseItem?.provided_context ?? []).join("\n\n"),
      criteria: caseItem?.criteria ?? "",
      tags: (caseItem?.tags ?? []).join(", "),
    },
  });
  const { errors, isDirty, isValid } = form.formState;

  const submit = form.handleSubmit(async (v) => {
    const body = {
      input: v.input.trim(),
      expected_output: v.expected_output.trim() || null,
      recorded_output: v.recorded_output.trim() || null,
      provided_context: chunks(v.context).length ? chunks(v.context) : null,
      criteria: v.criteria.trim() || null,
      tags: v.tags.split(/[,;]/).map((t) => t.trim()).filter(Boolean),
      metadata: (caseItem?.metadata as Record<string, unknown>) ?? {},
    };
    try {
      const out = caseItem
        ? await m.update.mutateAsync({ id: caseItem.id, ...body })
        : await m.create.mutateAsync({ dataset_id: datasetId, base_version_id: versionId, ...body });
      toast.success(`Saved as v${out.version.version_number}`);
      onSaved(out);
      onOpenChange(false);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        if (!o && isDirty && !window.confirm("Discard unsaved changes?")) return;
        onOpenChange(o);
      }}
    >
      <DialogContent side="right" title={caseItem ? `Case #${caseItem.position + 1}` : "Add a case"} description={caseItem ? `Case key ${caseItem.case_key.slice(0, 8)}` : undefined}>
        <form noValidate onSubmit={submit} className="space-y-4">
          {!readOnly && (
            <p className="flex items-start gap-2 rounded-md bg-info-bg px-3 py-2 text-sm text-info">
              <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
              Saving creates v{nextVersion}. Version v{nextVersion - 1} and runs that used it stay unchanged.
            </p>
          )}
          {readOnly && (
            <p className="rounded-md bg-muted px-3 py-2 text-sm text-muted-foreground">
              You are viewing an older version. Switch to the latest version to edit.
            </p>
          )}
          <fieldset disabled={readOnly} className="space-y-4">
            <Field label="Input" htmlFor="ce-input" error={errors.input?.message}>
              <Textarea id="ce-input" rows={3} aria-invalid={!!errors.input} {...form.register("input")} />
            </Field>
            <Field label="Reference answer" htmlFor="ce-ref">
              <Textarea id="ce-ref" rows={2} {...form.register("expected_output")} />
            </Field>
            <Field label="Recorded response" htmlFor="ce-resp" hint="A pre-collected model answer, scored when a run uses recorded responses.">
              <Textarea id="ce-resp" rows={3} {...form.register("recorded_output")} />
            </Field>
            <Field label="Context" htmlFor="ce-ctx" hint="Separate chunks with a blank line.">
              <Textarea id="ce-ctx" rows={4} {...form.register("context")} />
            </Field>
            <Field label="Evaluation criteria" htmlFor="ce-crit">
              <Textarea id="ce-crit" rows={2} {...form.register("criteria")} />
            </Field>
            <Field label="Tags" htmlFor="ce-tags" hint="Comma separated">
              <Input id="ce-tags" {...form.register("tags")} />
            </Field>
            {caseItem && Object.keys(caseItem.metadata ?? {}).length > 0 && (
              <div className="space-y-1">
                <span className="text-sm font-medium">Metadata</span>
                <pre className="max-h-32 overflow-auto rounded-md bg-muted p-2 text-xs">{JSON.stringify(caseItem.metadata, null, 2)}</pre>
              </div>
            )}
          </fieldset>
          {!readOnly && (
            <div className="flex items-center justify-between gap-2 border-t pt-4">
              {caseItem ? (
                <ConfirmButton
                  variant="ghost"
                  title="Delete this case?"
                  confirmLabel={`Delete and create v${nextVersion}`}
                  consequences={`The case is removed from v${nextVersion}. It stays in earlier versions and in past run results.`}
                  onConfirm={async () => {
                    try {
                      const out = await m.remove.mutateAsync(caseItem.id);
                      toast.success(`Deleted; now at v${out.version.version_number}`);
                      onSaved(out);
                      onOpenChange(false);
                    } catch (e) {
                      toast.error(errorMessage(e));
                    }
                  }}
                >
                  <Trash2 /> Delete
                </ConfirmButton>
              ) : (
                <span />
              )}
              <div className="flex gap-2">
                <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={!isValid || (!!caseItem && !isDirty)} loading={m.update.isPending || m.create.isPending}>
                  Save as v{nextVersion}
                </Button>
              </div>
            </div>
          )}
        </form>
      </DialogContent>
    </Dialog>
  );
}
