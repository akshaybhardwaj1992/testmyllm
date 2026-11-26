import { cn } from "@/lib/utils";

export const APP_NAME = "TestMyLLM";
export const TAGLINE = "Evaluate · Test · Analyze · Improve";

/** The robot-with-checklist mascot on a cream tile, so its white face and outlines read in light and dark themes. */
export function BrandMark({ className, size = "md" }: { className?: string; size?: "sm" | "md" | "lg" }) {
  const box = { sm: "size-7", md: "size-8", lg: "size-12" }[size];
  const img = { sm: "size-6", md: "size-7", lg: "size-11" }[size];
  return (
    <span
      className={cn("inline-flex shrink-0 items-center justify-center rounded-lg bg-brand-cream ring-1 ring-black/5 dark:ring-white/10", box, className)}
      aria-hidden
    >
      <img src="/logo-mark.png" alt="" className={img} draggable={false} />
    </span>
  );
}

/** "TestMy" in ink + "LLM" in brand orange, set in the logo's typeface. */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("font-display font-bold tracking-tight", className)}>
      TestMy<span className="text-primary">LLM</span>
    </span>
  );
}

export function Brand({ collapsed, className }: { collapsed?: boolean; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2", className)} aria-label={APP_NAME}>
      <BrandMark />
      {!collapsed && <Wordmark className="text-[17px]" />}
    </span>
  );
}
