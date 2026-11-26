import { Check, ChevronsUpDown, Search, Wrench } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { Checkbox, Input } from "@/components/ui/primitives";
import { useCatalogModels } from "@/hooks/queries";
import { cn } from "@/lib/utils";

const fmtPrice = (v: number | null | undefined) => (v == null ? "?" : v < 0.01 ? v.toFixed(3) : v < 1 ? v.toFixed(2) : v.toFixed(1));
const fmtCtx = (n: number | null | undefined) => (!n ? "" : n >= 1_000_000 ? `${Math.round(n / 1_000_000)}M ctx` : `${Math.round(n / 1000)}k ctx`);

/**
 * Searchable OpenRouter model picker. Unlike a <datalist>, it always lists every model when opened (even with
 * a value already set), searches ids and names, and can be limited to models that support tool calling.
 * Any id can still be typed (new models, private endpoints).
 */
export function ModelPicker({
  id,
  value,
  onChange,
  needs = "chat",
  placeholder = "Search models…",
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
  needs?: "chat" | "tools" | "embedding";
  placeholder?: string;
}) {
  const catalog = useCatalogModels();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [toolsOnly, setToolsOnly] = useState(needs === "tools");
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);

  const all = useMemo(
    () => (catalog.data ?? []).filter((m) => (needs === "embedding" ? m.kind === "embedding" : m.kind !== "embedding")),
    [catalog.data, needs],
  );
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all
      .filter((m) => !toolsOnly || m.supports_tools)
      .filter((m) => !q || m.id.toLowerCase().includes(q) || (m.name ?? "").toLowerCase().includes(q));
  }, [all, query, toolsOnly]);
  const current = all.find((m) => m.id === value.trim());

  useEffect(() => {
    const close = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);
  useEffect(() => setActive(0), [query, toolsOnly]);
  useEffect(() => {
    list.current?.querySelector(`[data-idx="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const pick = (mid: string) => {
    onChange(mid);
    setQuery("");
    setOpen(false);
  };

  return (
    <div ref={box} className="relative">
      <div className="relative">
        <Input
          id={id}
          role="combobox"
          aria-expanded={open}
          aria-controls={`${id}-list`}
          aria-autocomplete="list"
          autoComplete="off"
          className="pr-9 font-mono text-sm"
          placeholder={placeholder}
          value={open ? query || value : value}
          onFocus={(e) => {
            setOpen(true);
            setQuery("");
            e.target.select(); // typing replaces the current id; the list shows every model meanwhile
          }}
          onChange={(e) => {
            setQuery(e.target.value);
            onChange(e.target.value);
            setOpen(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setOpen(true);
              setActive((a) => Math.min(a + 1, Math.min(shown.length, 300) - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setActive((a) => Math.max(a - 1, 0));
            } else if (e.key === "Enter" && open && shown[active]) {
              e.preventDefault();
              pick(shown[active].id);
            } else if (e.key === "Escape") {
              setOpen(false);
            }
          }}
        />
        <ChevronsUpDown className="pointer-events-none absolute right-3 top-2.5 size-4 text-muted-foreground" aria-hidden />
      </div>
      {open && (
        <div className="absolute z-50 mt-1 w-full overflow-hidden rounded-md border bg-card text-foreground shadow-lg">
          <div className="flex items-center gap-3 border-b px-3 py-2 text-xs text-muted-foreground">
            <Search className="size-3.5" aria-hidden />
            <span className="flex-1">
              {catalog.isLoading ? "Loading models…" : `${shown.length} of ${all.length} models${query ? ` matching “${query}”` : ""}`}
            </span>
            {needs !== "embedding" && (
              <label className="flex cursor-pointer items-center gap-1.5">
                <Checkbox checked={toolsOnly} onCheckedChange={(v) => setToolsOnly(v === true)} aria-label="Only models that support tool calling" />
                Tool calling only
              </label>
            )}
          </div>
          <ul ref={list} id={`${id}-list`} role="listbox" className="max-h-72 overflow-y-auto py-1">
            {shown.slice(0, 300).map((m, i) => (
              <li
                key={m.id}
                data-idx={i}
                role="option"
                aria-selected={m.id === value}
                className={cn("flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm", i === active ? "bg-accent text-accent-foreground" : "hover:bg-accent/60")}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault();
                  pick(m.id);
                }}
              >
                <Check className={cn("size-3.5 shrink-0", m.id === value ? "opacity-100" : "opacity-0")} aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-mono text-xs">{m.id}</span>
                  {m.name && m.name !== m.id && <span className="block truncate text-xs text-muted-foreground">{m.name}</span>}
                </span>
                {m.supports_tools && (
                  <span className="inline-flex shrink-0 items-center gap-0.5 rounded bg-muted px-1 text-[10px] text-muted-foreground" title="Supports tool calling">
                    <Wrench className="size-3" /> tools
                  </span>
                )}
                <span className="shrink-0 text-right text-[11px] tabular-nums text-muted-foreground">
                  ${fmtPrice(m.price_in_per_mtok)} / ${fmtPrice(m.price_out_per_mtok)}
                  <span className="block">{fmtCtx(m.context_length)}</span>
                </span>
              </li>
            ))}
            {!catalog.isLoading && shown.length === 0 && (
              <li className="px-3 py-3 text-sm text-muted-foreground">
                No model matches. {toolsOnly ? "Untick “Tool calling only” or " : ""}keep typing to use a custom id.
              </li>
            )}
            {shown.length > 300 && <li className="px-3 py-2 text-xs text-muted-foreground">Showing the first 300; type to narrow down.</li>}
          </ul>
          <p className="border-t px-3 py-1.5 text-[11px] text-muted-foreground">Price per million tokens (input / output) from OpenRouter.</p>
        </div>
      )}
      {!open && value.trim() && catalog.data && !current && (
        <p className="mt-1 text-xs text-warn">Not in the OpenRouter catalog; check the id.</p>
      )}
      {!open && needs === "tools" && current && !current.supports_tools && (
        <p className="mt-1 text-xs text-warn">OpenRouter does not list tool calling for this model; agent runs may fail.</p>
      )}
    </div>
  );
}
