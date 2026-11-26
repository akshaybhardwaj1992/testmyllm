import {
  Bot,
  Boxes,
  Database,
  FlaskConical,
  GitCompareArrows,
  History,
  KeyRound,
  LayoutDashboard,
  ListChecks,
  MessageSquareText,
  PlayCircle,
  Plug,
  Scale,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  label: string;
  path: string; // relative to /p/:projectId/
  icon: LucideIcon;
  milestone?: string; // set = not built yet
  disabled?: boolean; // "Coming later" (later phase)
};

export type NavGroup = { label: string; items: NavItem[] };

/** Navigation areas from spec §3. Unbuilt screens show which milestone delivers them. */
export const NAV: NavGroup[] = [
  { label: "Overview", items: [{ label: "Overview", path: "overview", icon: LayoutDashboard }] },
  {
    label: "Test Studio",
    items: [
      { label: "LLM Response Testing", path: "studio/llm", icon: MessageSquareText },
      { label: "RAG Evaluation", path: "studio/rag", icon: FlaskConical },
      { label: "Model Comparison", path: "studio/compare", icon: Scale },
      { label: "Agent Testing", path: "studio/agents", icon: Bot },
    ],
  },
  {
    label: "Test Management",
    items: [
      { label: "Datasets", path: "datasets", icon: Database },
      { label: "Test Cases", path: "cases", icon: ListChecks },
      { label: "Test Suites", path: "suites", icon: Boxes },
      { label: "Test Runs", path: "runs", icon: PlayCircle },
    ],
  },
  {
    label: "Regression Testing",
    items: [
      { label: "Baselines & Compare", path: "regression", icon: GitCompareArrows },
      { label: "Quality Gates", path: "gates", icon: ShieldCheck },
    ],
  },
  {
    label: "Security Testing",
    items: [{ label: "AI Security Testing", path: "security", icon: ShieldAlert }],
  },
  { label: "History", items: [{ label: "Evaluation History", path: "history", icon: History }] },
  {
    label: "Settings",
    items: [
      { label: "Providers & API Keys", path: "settings/providers", icon: KeyRound },
      { label: "Evaluation Models", path: "settings/judges", icon: Sparkles },
      { label: "Project Settings", path: "settings/project", icon: Settings2 },
      { label: "Integrations", path: "settings/integrations", icon: Plug },
    ],
  },
];

export const ALL_ITEMS = NAV.flatMap((g) => g.items.map((i) => ({ ...i, group: g.label })));
