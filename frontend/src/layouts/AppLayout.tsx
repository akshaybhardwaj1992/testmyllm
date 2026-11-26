import {
  ChevronRight,
  ChevronsUpDown,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Search,
  Sun,
} from "lucide-react";
import { Fragment, useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useNavigate, useParams } from "react-router-dom";

import { Brand } from "@/components/brand";
import { CommandPalette } from "@/components/command-palette";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Tooltip,
} from "@/components/ui/overlay";
import { useAuth } from "@/hooks/auth";
import { useProject, useProjects } from "@/hooks/queries";
import { NAV } from "@/layouts/nav";
import { cn } from "@/lib/utils";

function readStored(key: string, fallback: string) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function useTheme() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try {
      localStorage.setItem("theme", dark ? "dark" : "light");
    } catch {
      /* storage unavailable: theme still applies for this session */
    }
  }, [dark]);
  return [dark, setDark] as const;
}

export function ProjectLayout() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const [collapsed, setCollapsed] = useState(() => readStored("sidebar", "open") === "collapsed");

  useEffect(() => {
    try {
      localStorage.setItem("sidebar", collapsed ? "collapsed" : "open");
      localStorage.setItem("lastProject", String(pid));
    } catch {
      /* ignore */
    }
  }, [collapsed, pid]);

  return (
    <div className="flex h-screen overflow-hidden">
      <aside
        className={cn(
          "flex shrink-0 flex-col border-r bg-sidebar transition-[width] duration-200",
          collapsed ? "w-14" : "w-64",
        )}
        aria-label="Main navigation"
      >
        <Link to="/projects" className="flex h-14 items-center border-b px-3" aria-label="TestMyLLM home">
          <Brand collapsed={collapsed} />
        </Link>
        <nav className="flex-1 overflow-y-auto px-2 py-2">
          {NAV.map((group) => (
            <div key={group.label} className="mb-2">
              {!collapsed && (
                <div className="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {group.label}
                </div>
              )}
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const inner = (
                    <>
                      <item.icon className="size-4 shrink-0" aria-hidden />
                      {!collapsed && <span className="flex-1 truncate">{item.label}</span>}
                      {!collapsed && (item.milestone || item.disabled) && (
                        <span className="rounded bg-muted px-1 text-[10px] text-muted-foreground">
                          {item.disabled ? "Later" : item.milestone}
                        </span>
                      )}
                    </>
                  );
                  const cls = "flex h-7 items-center gap-2.5 rounded-md px-2 text-sm";
                  return (
                    <li key={item.path}>
                      <Tooltip content={collapsed ? item.label : item.disabled ? "Coming later" : null} side="right">
                        {item.disabled ? (
                          <span aria-disabled className={cn(cls, "cursor-not-allowed text-muted-foreground/60")}>
                            {inner}
                          </span>
                        ) : (
                          <NavLink
                            to={`/p/${pid}/${item.path}`}
                            aria-label={collapsed ? item.label : undefined}
                            className={({ isActive }) =>
                              cn(
                                cls,
                                isActive
                                  ? "bg-accent font-medium text-accent-foreground"
                                  : "text-foreground/80 hover:bg-accent/60",
                                item.milestone && !isActive && "text-muted-foreground",
                              )
                            }
                          >
                            {inner}
                          </NavLink>
                        )}
                      </Tooltip>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
        <div className="border-t p-2">
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
            {!collapsed && "Collapse"}
          </Button>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar projectId={pid} />
        <main className="flex-1 overflow-y-auto">
          <Outlet />
        </main>
      </div>
      <CommandPalette projectId={pid} />
    </div>
  );
}

export function TopBar({ projectId }: { projectId?: number }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const projects = useProjects();
  const current = projects.data?.find((p) => p.id === projectId);
  const [dark, setDark] = useTheme();

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-card px-4">
      {projectId ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm" className="max-w-64 justify-between" aria-label="Switch project">
              <span className="truncate">{current?.name ?? "Project"}</span>
              <ChevronsUpDown className="opacity-60" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuLabel>Projects</DropdownMenuLabel>
            {projects.data?.map((p) => (
              <DropdownMenuItem key={p.id} onSelect={() => navigate(`/p/${p.id}/overview`)}>
                <span className="flex-1 truncate">{p.name}</span>
                {p.id === projectId && <span className="text-xs text-muted-foreground">current</span>}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => navigate("/projects?new=1")}>
              <Plus /> New project
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => navigate("/projects")}>All projects</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <Link to="/projects" aria-label="TestMyLLM home">
          <Brand />
        </Link>
      )}

      <button
        type="button"
        onClick={() => window.dispatchEvent(new Event("testlab:palette"))}
        className="ml-2 flex h-8 w-72 cursor-pointer items-center gap-2 rounded-md border bg-background px-2.5 text-sm text-muted-foreground hover:bg-accent/50"
        aria-label="Open command palette"
      >
        <Search className="size-4" aria-hidden />
        <span className="flex-1 text-left">Search…</span>
        <kbd className="rounded border bg-muted px-1.5 font-mono text-[10px]">Ctrl K</kbd>
      </button>

      <div className="ml-auto flex items-center gap-1">
        <Tooltip content={dark ? "Switch to light theme" : "Switch to dark theme"}>
          <Button variant="ghost" size="icon" onClick={() => setDark(!dark)} aria-label="Toggle theme">
            {dark ? <Sun /> : <Moon />}
          </Button>
        </Tooltip>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="sm" aria-label="User menu">
              <span className="flex size-6 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary">
                {user?.email?.[0]?.toUpperCase()}
              </span>
              <span className="max-w-40 truncate">{user?.email}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuLabel>{user?.email}</DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onSelect={async () => {
                await logout();
                navigate("/login");
              }}
            >
              <LogOut /> Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

export function PageHeader({
  crumbs,
  title,
  description,
  actions,
}: {
  crumbs?: { label: string; to?: string }[];
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  const { projectId } = useParams();
  const project = useProject(Number(projectId ?? 0));
  const trail = [
    ...(project.data ? [{ label: project.data.name, to: `/p/${project.data.id}/overview` }] : []),
    ...(crumbs ?? []),
  ];
  return (
    <div className="space-y-2 border-b bg-card/40 px-6 pb-4 pt-4">
      {trail.length > 0 && (
        <nav aria-label="Breadcrumb">
          <ol className="flex items-center gap-1 text-xs text-muted-foreground">
            {trail.map((c, i) => (
              <Fragment key={i}>
                {i > 0 && <ChevronRight className="size-3" aria-hidden />}
                <li>
                  {c.to && i < trail.length - 1 ? (
                    <Link to={c.to} className="hover:text-foreground hover:underline">
                      {c.label}
                    </Link>
                  ) : (
                    <span aria-current={i === trail.length - 1 ? "page" : undefined}>{c.label}</span>
                  )}
                </li>
              </Fragment>
            ))}
          </ol>
        </nav>
      )}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 space-y-1">
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </div>
    </div>
  );
}
