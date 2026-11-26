import * as DialogPrimitive from "@radix-ui/react-dialog";
import { FolderKanban, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { useProjects } from "@/hooks/queries";
import { ALL_ITEMS } from "@/layouts/nav";
import { cn } from "@/lib/utils";

type Entry = { id: string; label: string; hint: string; icon: typeof Search; go: () => void };

/** Ctrl/Cmd+K: jump to any screen or project. Fully keyboard operable. */
export function CommandPalette({ projectId }: { projectId?: number }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [active, setActive] = useState(0);
  const navigate = useNavigate();
  const projects = useProjects();

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("testlab:palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("testlab:palette", onOpen);
    };
  }, []);

  const entries = useMemo<Entry[]>(() => {
    const close = (fn: () => void) => () => {
      setOpen(false);
      setQ("");
      fn();
    };
    const screens: Entry[] = projectId
      ? ALL_ITEMS.filter((i) => !i.disabled).map((i) => ({
          id: `nav-${i.path}`,
          label: i.label,
          hint: i.milestone ? `${i.group} · coming in ${i.milestone}` : i.group,
          icon: i.icon,
          go: close(() => navigate(`/p/${projectId}/${i.path}`)),
        }))
      : [];
    const projs: Entry[] = (projects.data ?? []).map((p) => ({
      id: `proj-${p.id}`,
      label: p.name,
      hint: "Project",
      icon: FolderKanban,
      go: close(() => navigate(`/p/${p.id}/overview`)),
    }));
    const all = [...screens, ...projs];
    const needle = q.trim().toLowerCase();
    return needle ? all.filter((e) => `${e.label} ${e.hint}`.toLowerCase().includes(needle)) : all;
  }, [projectId, projects.data, q, navigate]);

  useEffect(() => setActive(0), [q]);

  return (
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-black/40" />
        <DialogPrimitive.Content
          className="fixed left-1/2 top-[15vh] z-50 w-[min(560px,92vw)] -translate-x-1/2 overflow-hidden rounded-lg border bg-card shadow-2xl"
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setActive((a) => Math.min(a + 1, entries.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              entries[active]?.go();
            }
          }}
        >
          <DialogPrimitive.Title className="sr-only">Command palette</DialogPrimitive.Title>
          <DialogPrimitive.Description className="sr-only">Search screens and projects</DialogPrimitive.Description>
          <div className="flex items-center gap-2 border-b px-3">
            <Search className="size-4 text-muted-foreground" aria-hidden />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search screens and projects…"
              aria-label="Search screens and projects"
              role="combobox"
              aria-expanded
              aria-controls="palette-list"
              aria-activedescendant={entries[active]?.id}
              className="h-11 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <ul id="palette-list" role="listbox" className="max-h-80 overflow-y-auto p-1">
            {entries.length === 0 && <li className="px-3 py-6 text-center text-sm text-muted-foreground">No matches</li>}
            {entries.map((e, i) => (
              <li
                key={e.id}
                id={e.id}
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={e.go}
                className={cn(
                  "flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm",
                  i === active && "bg-accent text-accent-foreground",
                )}
              >
                <e.icon className="size-4 text-muted-foreground" aria-hidden />
                <span className="flex-1">{e.label}</span>
                <span className="text-xs text-muted-foreground">{e.hint}</span>
              </li>
            ))}
          </ul>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
