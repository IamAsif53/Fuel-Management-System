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
      className="flex min-w-0 flex-col justify-between rounded-lg border border-bg-border bg-bg-panel p-3 sm:p-3.5 transition hover:border-bg-border/80 shadow-sm"
    >
      <div
        title={label}
        className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted truncate"
      >
        {label}
      </div>
      <div className="mt-2 flex flex-col gap-1.5 min-w-0">
        <div
          title={value}
          className={clsx(
            "text-xl sm:text-2xl font-bold tabular-nums whitespace-nowrap tracking-tight overflow-hidden text-ellipsis",
            toneColors[tone]
          )}
        >
          {value}
        </div>
        {sub && (
          <div className="flex min-w-0">
            <span
              title={sub}
              className={clsx(
                "inline-block max-w-full truncate rounded border px-1.5 py-0.5 text-[10px] font-medium leading-tight whitespace-nowrap",
                badgeColors[tone]
              )}
            >
              {sub}
            </span>
          </div>
        )}
      </div>
    </div>
  );
}
