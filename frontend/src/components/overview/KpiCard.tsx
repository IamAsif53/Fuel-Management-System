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

  // Adjust font size for longer textual values like NORMAL, DEGRADED, SAFE_HOLD
  const isLongText = value.length > 5 && !value.includes("%") && !value.includes("L");

  return (
    <div
      data-testid={testId || `kpi-${label.toLowerCase().replace(/\s+/g, "-")}`}
      className="flex min-w-0 flex-col justify-between rounded-2xl border border-bg-border bg-bg-panel/95 p-3 sm:p-3.5 shadow-panel transition hover:border-brand/25"
    >
      <div
        title={label}
        className="h-7 flex items-center text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-muted leading-tight truncate"
      >
        {label}
      </div>
      <div className="mt-1 flex flex-col gap-1 min-w-0">
        <div
          title={value}
          className={clsx(
            "font-bold tabular-nums whitespace-nowrap tracking-tight overflow-hidden text-ellipsis leading-none",
            isLongText ? "text-lg xl:text-xl" : "text-xl xl:text-2xl",
            toneColors[tone]
          )}
        >
          {value}
        </div>
        {sub && (
          <div className="mt-1 flex min-w-0">
            <span
              title={sub}
              className={clsx(
                "inline-block max-w-full truncate rounded border px-1.5 py-0.5 text-[10px] font-medium leading-none whitespace-nowrap",
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
