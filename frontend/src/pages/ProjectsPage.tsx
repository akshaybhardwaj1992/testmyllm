import { zodResolver } from "@hookform/resolvers/zod";
import { FolderKanban, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useForm } from "react-hook-form";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { EmptyState, ErrorState, TableSkeleton } from "@/components/states";
import { StatusChip } from "@/components/status";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent } from "@/components/ui/overlay";
import { Badge, Card, Field, Input, Textarea } from "@/components/ui/primitives";
import { useCreateProject, useProjects } from "@/hooks/queries";
import { TopBar } from "@/layouts/AppLayout";
import { fmtDate } from "@/lib/utils";
import { errorMessage } from "@/services/api";

const schema = z.object({
  name: z.string().trim().min(1, "Give the project a name").max(200),
  description: z.string().max(5000),
});

function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const create = useCreateProject();
  const navigate = useNavigate();
  const form = useForm<z.infer<typeof schema>>({
    resolver: zodResolver(schema),
    mode: "onChange",
    defaultValues: { name: "", description: "" },
  });
  const { errors, isValid } = form.formState;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="New project" description="A project holds providers, judges, test runs and results.">
        <form
          noValidate
          className="space-y-4"
          onSubmit={form.handleSubmit(async (v) => {
            try {
              const p = await create.mutateAsync(v);
              toast.success(`Project "${p.name}" created`);
              onOpenChange(false);
              navigate(`/p/${p.id}/settings/providers`);
            } catch (e) {
              toast.error(errorMessage(e));
            }
          })}
        >
          <Field label="Name" htmlFor="pname" error={errors.name?.message}>
            <Input id="pname" autoFocus aria-invalid={!!errors.name} {...form.register("name")} />
          </Field>
          <Field label="Description (optional)" htmlFor="pdesc">
            <Textarea id="pdesc" rows={3} {...form.register("description")} />
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={!isValid} loading={create.isPending}>
              Create project
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function ProjectsPage() {
  const projects = useProjects();
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState(params.get("new") === "1");
  useEffect(() => {
    if (params.get("new") === "1") setParams({}, { replace: true });
  }, [params, setParams]);

  return (
    <div className="min-h-screen">
      <TopBar />
      <div className="mx-auto max-w-6xl space-y-6 px-6 py-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">Projects</h1>
            <p className="text-sm text-muted-foreground">Each project has its own providers, judges and test history.</p>
          </div>
          <Button onClick={() => setOpen(true)}>
            <Plus /> New project
          </Button>
        </div>
        {projects.isLoading && (
          <Card>
            <TableSkeleton rows={3} cols={4} />
          </Card>
        )}
        {projects.isError && <ErrorState error={projects.error} onRetry={() => projects.refetch()} />}
        {projects.data?.length === 0 && (
          <EmptyState
            icon={FolderKanban}
            title="No projects yet"
            description="Create a project, add your OpenRouter key, and run your first evaluation."
            action={
              <Button onClick={() => setOpen(true)}>
                <Plus /> New project
              </Button>
            }
          />
        )}
        {!!projects.data?.length && (
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-3">
            {projects.data.map((p) => (
              <Link
                key={p.id}
                to={`/p/${p.id}/overview`}
                className="group rounded-lg border bg-card p-5 shadow-xs transition-colors hover:border-primary/50"
              >
                <div className="flex items-start justify-between gap-2">
                  <h2 className="font-semibold group-hover:text-primary">{p.name}</h2>
                  <Badge variant="outline">{p.role}</Badge>
                </div>
                <p className="mt-1 line-clamp-2 min-h-10 text-sm text-muted-foreground">
                  {p.description || "No description"}
                </p>
                <div className="mt-4 flex items-center justify-between border-t pt-3 text-xs text-muted-foreground">
                  {p.last_run ? (
                    <>
                      <span className="truncate">Last run {fmtDate(p.last_run.created_at)}</span>
                      <span className="flex items-center gap-2">
                        {p.last_run.pass_rate != null && (
                          <span className="tabular font-medium text-foreground">
                            {Math.round(p.last_run.pass_rate * 100)}% pass
                          </span>
                        )}
                        <StatusChip status={p.last_run.status} />
                      </span>
                    </>
                  ) : (
                    <span>No runs yet</span>
                  )}
                </div>
              </Link>
            ))}
          </div>
        )}
      </div>
      <NewProjectDialog open={open} onOpenChange={setOpen} />
    </div>
  );
}
