import { zodResolver } from "@hookform/resolvers/zod";
import { Trash2, UserPlus } from "lucide-react";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import { z } from "zod";

import { ConfirmButton, ErrorState, TableSkeleton } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input, NativeSelect, Switch, Textarea } from "@/components/ui/primitives";
import { useAuth } from "@/hooks/auth";
import { useMembers, useProject, useProjectMutations } from "@/hooks/queries";
import { PageHeader } from "@/layouts/AppLayout";
import { errorMessage } from "@/services/api";
import type { ProjectSettings, Role } from "@/types";

function DataHandlingCard({ pid, settings, isOwner }: { pid: number; settings: ProjectSettings; isOwner: boolean }) {
  const pm = useProjectMutations(pid);
  const [retain, setRetain] = useState(settings.retention_days != null);
  const [days, setDays] = useState(String(settings.retention_days ?? 90));
  const [noPersist, setNoPersist] = useState(!!settings.no_persistence);
  const [hours, setHours] = useState(String(settings.no_persistence_hours ?? 24));
  const dirty =
    retain !== (settings.retention_days != null) ||
    (retain && Number(days) !== settings.retention_days) ||
    noPersist !== !!settings.no_persistence ||
    Number(hours) !== settings.no_persistence_hours;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Data retention & privacy</CardTitle>
        <CardDescription>
          Datasets may contain personal data. Choose how long run results are kept. Deletion runs every 15 minutes and removes results, judge reasons, exports and
          ad-hoc cases. Runs saved as baselines are never deleted by retention.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <label className="flex items-start gap-3 text-sm">
          <Switch checked={retain} onCheckedChange={setRetain} disabled={!isOwner} />
          <span className="flex-1">
            Delete finished runs older than
            <span className="mx-2 inline-flex items-center gap-1">
              <Input aria-label="Retention days" type="number" min={1} max={3650} className="h-8 w-20" value={days} disabled={!isOwner || !retain} onChange={(e) => setDays(e.target.value)} />
              days
            </span>
            <span className="block text-xs text-muted-foreground">Off = keep runs until you delete them.</span>
          </span>
        </label>
        <label className="flex items-start gap-3 text-sm">
          <Switch checked={noPersist} onCheckedChange={setNoPersist} disabled={!isOwner} />
          <span className="flex-1">
            No-persistence mode: delete each new run
            <span className="mx-2 inline-flex items-center gap-1">
              <Input aria-label="Hours to keep" type="number" min={1} max={168} className="h-8 w-20" value={hours} disabled={!isOwner || !noPersist} onChange={(e) => setHours(e.target.value)} />
              hours
            </span>
            after it starts
            <span className="block text-xs text-muted-foreground">
              For sensitive data: results are kept only long enough to review and export them, never cached for re-use, excluded from trends, and cannot become baselines.
              Applies to runs started after you turn it on.
            </span>
          </span>
        </label>
        {isOwner ? (
          <Button
            disabled={!dirty}
            loading={pm.update.isPending}
            onClick={async () => {
              try {
                await pm.update.mutateAsync({
                  settings: { retention_days: retain ? Number(days) : null, no_persistence: noPersist, no_persistence_hours: Number(hours) },
                });
                toast.success("Data settings saved");
              } catch (e) {
                toast.error(errorMessage(e));
              }
            }}
          >
            Save data settings
          </Button>
        ) : (
          <p className="text-xs text-muted-foreground">Only project owners can change data settings.</p>
        )}
      </CardContent>
    </Card>
  );
}

const general = z.object({ name: z.string().trim().min(1, "Name is required").max(200), description: z.string().max(5000) });
const member = z.object({
  email: z.string().trim().email("Enter a valid email"),
  role: z.enum(["owner", "editor", "viewer"]),
  initial_password: z.string().refine((v) => v === "" || v.length >= 10, "At least 10 characters"),
});

const ROLE_HELP: Record<Role, string> = {
  owner: "Everything, including provider keys and members",
  editor: "Datasets, suites, judges and runs",
  viewer: "Read-only",
};

export function ProjectSettingsPage() {
  const pid = Number(useParams().projectId);
  const project = useProject(pid);
  const members = useMembers(pid);
  const pm = useProjectMutations(pid);
  const { user } = useAuth();
  const navigate = useNavigate();
  const isOwner = project.data?.role === "owner";
  const [needsPassword, setNeedsPassword] = useState(false);

  const gForm = useForm<z.infer<typeof general>>({
    resolver: zodResolver(general),
    mode: "onChange",
    values: { name: project.data?.name ?? "", description: project.data?.description ?? "" },
  });
  const mForm = useForm<z.infer<typeof member>>({
    resolver: zodResolver(member),
    mode: "onChange",
    defaultValues: { email: "", role: "viewer", initial_password: "" },
  });

  if (project.isLoading) return <div className="p-6"><TableSkeleton rows={6} /></div>;
  if (project.isError || !project.data) return <div className="p-6"><ErrorState error={project.error} onRetry={() => project.refetch()} /></div>;

  return (
    <>
      <PageHeader crumbs={[{ label: "Settings" }, { label: "Project Settings" }]} title="Project Settings" />
      <div className="max-w-4xl space-y-6 p-6">
        <Card>
          <CardHeader>
            <CardTitle>General</CardTitle>
          </CardHeader>
          <CardContent>
            <form
              noValidate
              className="space-y-4"
              onSubmit={gForm.handleSubmit(async (v) => {
                try {
                  await pm.update.mutateAsync(v);
                  toast.success("Project updated");
                  gForm.reset(v);
                } catch (e) {
                  toast.error(errorMessage(e));
                }
              })}
            >
              <fieldset disabled={!isOwner} className="space-y-4">
                <Field label="Name" htmlFor="ps-name" error={gForm.formState.errors.name?.message}>
                  <Input id="ps-name" {...gForm.register("name")} />
                </Field>
                <Field label="Description" htmlFor="ps-desc">
                  <Textarea id="ps-desc" rows={3} {...gForm.register("description")} />
                </Field>
              </fieldset>
              {isOwner && (
                <div className="flex justify-end">
                  <Button type="submit" disabled={!gForm.formState.isDirty || !gForm.formState.isValid} loading={pm.update.isPending}>
                    Save changes
                  </Button>
                </div>
              )}
            </form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Members & roles</CardTitle>
            <CardDescription>Owners manage keys and members; editors run tests; viewers read results.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {members.isLoading && <TableSkeleton rows={2} cols={3} />}
            {members.data && (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th className="py-2 font-medium">Email</th>
                    <th className="py-2 font-medium">Role</th>
                    <th className="py-2" />
                  </tr>
                </thead>
                <tbody>
                  {members.data.map((m) => (
                    <tr key={m.user_id} className="border-b last:border-0">
                      <td className="py-2">
                        {m.email} {m.email === user?.email && <span className="text-xs text-muted-foreground">(you)</span>}
                      </td>
                      <td className="w-48 py-2">
                        <NativeSelect
                          aria-label={`Role for ${m.email}`}
                          value={m.role}
                          disabled={!isOwner}
                          onChange={async (e) => {
                            try {
                              await pm.setRole.mutateAsync({ userId: m.user_id, role: e.target.value as Role });
                              toast.success(`${m.email} is now ${e.target.value}`);
                            } catch (err) {
                              toast.error(errorMessage(err));
                            }
                          }}
                        >
                          <option value="owner">Owner</option>
                          <option value="editor">Editor</option>
                          <option value="viewer">Viewer</option>
                        </NativeSelect>
                      </td>
                      <td className="w-12 py-2 text-right">
                        {isOwner && (
                          <ConfirmButton
                            variant="ghost"
                            size="icon"
                            title={`Remove ${m.email}?`}
                            confirmLabel="Remove member"
                            consequences="They lose access to this project immediately. Their account and past reviews are kept."
                            onConfirm={async () => {
                              try {
                                await pm.removeMember.mutateAsync(m.user_id);
                                toast.success("Member removed");
                              } catch (e) {
                                toast.error(errorMessage(e));
                              }
                            }}
                          >
                            <Trash2 aria-label={`Remove ${m.email}`} />
                          </ConfirmButton>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {isOwner && (
              <form
                noValidate
                className="grid grid-cols-[1fr_160px_auto] items-end gap-3 border-t pt-4"
                onSubmit={mForm.handleSubmit(async (v) => {
                  try {
                    const out = await pm.addMember.mutateAsync({ email: v.email, role: v.role, initial_password: v.initial_password || null });
                    toast.success(out.created_user ? `Account created for ${out.email}. Share the initial password with them securely.` : `${out.email} added`);
                    mForm.reset();
                    setNeedsPassword(false);
                  } catch (e) {
                    if (e instanceof Error && /initial password/i.test(e.message)) setNeedsPassword(true);
                    toast.error(errorMessage(e));
                  }
                })}
              >
                <Field label="Add member by email" htmlFor="m-email" error={mForm.formState.errors.email?.message}>
                  <Input id="m-email" type="email" {...mForm.register("email")} />
                </Field>
                <Field label="Role" htmlFor="m-role">
                  <NativeSelect id="m-role" {...mForm.register("role")}>
                    <option value="viewer">Viewer</option>
                    <option value="editor">Editor</option>
                    <option value="owner">Owner</option>
                  </NativeSelect>
                </Field>
                <Button type="submit" disabled={!mForm.formState.isValid} loading={pm.addMember.isPending}>
                  <UserPlus /> Add
                </Button>
                {needsPassword && (
                  <Field
                    className="col-span-3"
                    label="Initial password for the new account"
                    htmlFor="m-pw"
                    hint="No account exists for this email. Set an initial password (10+ characters) and share it securely."
                    error={mForm.formState.errors.initial_password?.message}
                  >
                    <Input id="m-pw" type="password" autoComplete="new-password" {...mForm.register("initial_password")} />
                  </Field>
                )}
                <p className="col-span-3 text-xs text-muted-foreground">
                  {(["owner", "editor", "viewer"] as Role[]).map((r) => `${r[0].toUpperCase() + r.slice(1)}: ${ROLE_HELP[r]}`).join(" · ")}
                </p>
              </form>
            )}
          </CardContent>
        </Card>

        <DataHandlingCard pid={pid} settings={project.data.settings} isOwner={isOwner} />

        {isOwner && (
          <Card className="border-destructive/40">
            <CardHeader>
              <CardTitle className="text-destructive">Danger zone</CardTitle>
              <CardDescription>Deleting the project removes its providers, judges, datasets, suites, runs and results.</CardDescription>
            </CardHeader>
            <CardContent>
              <ConfirmButton
                title={`Delete project "${project.data.name}"?`}
                confirmLabel="Delete project permanently"
                consequences="This cannot be undone. Encrypted provider keys, datasets, runs and all results are deleted for every member."
                onConfirm={async () => {
                  try {
                    await pm.remove.mutateAsync();
                    try {
                      localStorage.removeItem("lastProject");
                    } catch {
                      /* ignore */
                    }
                    toast.success("Project deleted");
                    navigate("/projects");
                  } catch (e) {
                    toast.error(errorMessage(e));
                  }
                }}
              >
                <Trash2 /> Delete project
              </ConfirmButton>
            </CardContent>
          </Card>
        )}
      </div>
    </>
  );
}
