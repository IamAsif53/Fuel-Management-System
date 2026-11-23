import clsx from "clsx";
import type { EngineMode } from "@/api/types";

interface EngineModeBadgeProps {
  mode: EngineMode | string | undefined;
}

export function EngineModeBadge({ mode = "NORMAL" }: EngineModeBadgeProps) {
  const configs: Record<string, { cls: string; label: string }> = {
    NORMAL: {
      cls: "border-brand/40 bg-brand-soft text-brand",
      label: "NORMAL",
    },
    DEGRADED: {
      cls: "border-state-degraded/40 bg-state-degraded/20 text-state-degraded",
      label: "DEGRADED",
    },
    SAFE_HOLD: {
      cls: "border-state-down/40 bg-state-down/20 text-state-down",
      label: "SAFE HOLD",
    },
    NO_FEASIBLE_ROUTES: {
      cls: "border-state-down/40 bg-state-down/20 text-state-down",
      label: "NO ROUTES",
    },
  };

  const cfg = configs[mode] ?? {
    cls: "border-bg-border bg-bg-raised text-ink-muted",
    label: mode,
  };

  return (
    <span
      data-testid="engine-mode"
      className={clsx(
        "inline-flex items-center rounded border px-2 py-0.5 text-xs font-semibold tracking-wider uppercase",
        cfg.cls
      )}
    >
      {cfg.label}
    </span>
  );
}
