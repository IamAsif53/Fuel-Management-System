import clsx from "clsx";
import type { ConnectionState } from "@/hooks/useLiveState";

interface ConnectionDotProps {
  connection: ConnectionState;
}

export function ConnectionDot({ connection }: ConnectionDotProps) {
  const configs = {
    live: {
      color: "bg-state-healthy",
      pulse: "animate-pulse",
      text: "LIVE",
      textColor: "text-state-healthy",
    },
    polling: {
      color: "bg-state-degraded",
      pulse: "",
      text: "POLLING",
      textColor: "text-state-degraded",
    },
    offline: {
      color: "bg-state-down",
      pulse: "",
      text: "OFFLINE",
      textColor: "text-state-down",
    },
  };

  const cfg = configs[connection] ?? configs.offline;

  return (
    <div
      data-testid="connection"
      className="inline-flex items-center gap-1.5 rounded-full border border-bg-border bg-bg-panel px-2.5 py-1 text-xs font-medium"
      title={`Connection: ${cfg.text}`}
    >
      <span className={clsx("h-2 w-2 rounded-full", cfg.color, cfg.pulse)} />
      <span className={clsx("font-semibold tracking-wider", cfg.textColor)}>
        {cfg.text}
      </span>
    </div>
  );
}
