import clsx from "clsx";

export interface KpiCardProps {
  label: string;
  value: string;
  sub?: string;
  tone?: "ok" | "warn" | "bad" | "neutral";
  testId?: string;
}

export function KpiCard({
  label,
  value,
  sub,
  tone = "neutral",
  testId,
}: KpiCardProps) {
  const toneColors = {
    ok: "text-state-healthy",
    warn: "text-state-degraded",
    bad: "text-state-down",
    neutral: "text-ink",
  };

  const badgeColors = {
    ok: "bg-state-healthy/10 text-state-healthy border-state-healthy/20",
    warn: "bg-state-degraded/10 text-state-degraded border-state-degraded/20",
    bad: "bg-state-down/10 text-state-down border-state-down/20",
    neutral: "bg-bg-raised text-ink-muted border-bg-border",
  };

  return (
    <div
      data-testid={testId || `kpi-${label.toLowerCase().replace(/\s+/g, "-")}`}
      className="flex flex-col justify-between rounded-lg border border-bg-border bg-bg-panel p-4 transition hover:border-bg-border/80"
    >
      <div className="mb-2 text-xs font-medium uppercase tracking-wider text-ink-muted">
        {label}
      </div>
      <div className="flex items-baseline justify-between gap-2">
        <div className={clsx("text-2xl font-semibold tabular-nums", toneColors[tone])}>
          {value}
        </div>
        {sub && (
          <span
            className={clsx(
              "rounded border px-1.5 py-0.5 text-[11px] font-medium",
              badgeColors[tone]
            )}
          >
            {sub}
          </span>
        )}
      </div>
    </div>
  );
}
