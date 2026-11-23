import { NavLink } from "react-router-dom";
import clsx from "clsx";
import {
  Gauge,
  Network,
  ShieldAlert,
  Inbox,
  History,
  Bell,
  FlaskConical,
  Bot,
  HeartPulse,
  Settings2,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  Icon: LucideIcon;
  phase: number;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/overview", label: "Overview", Icon: Gauge, phase: 3 },
  { to: "/network", label: "Network", Icon: Network, phase: 3 },
  { to: "/risk", label: "Risk", Icon: ShieldAlert, phase: 6 },
  { to: "/recommendations", label: "Recommendations", Icon: Inbox, phase: 4 },
  { to: "/decisions", label: "Decisions", Icon: History, phase: 4 },
  { to: "/alerts", label: "Alerts", Icon: Bell, phase: 6 },
  { to: "/whatif", label: "What-if", Icon: FlaskConical, phase: 8 },
  { to: "/assistant", label: "Assistant", Icon: Bot, phase: 9 },
  { to: "/health", label: "System Health", Icon: HeartPulse, phase: 3 },
  { to: "/control", label: "Control (demo)", Icon: Settings2, phase: 10 },
];

export function Sidebar() {
  return (
    <nav
      className="flex w-14 shrink-0 flex-col gap-1 border-r border-bg-border bg-bg-panel p-2 transition-all xl:w-56"
      aria-label="Main Navigation"
    >
      {NAV_ITEMS.map(({ to, label, Icon, phase }) => (
        <NavLink
          key={to}
          to={to}
          title={`${label} (Phase ${String(phase).padStart(2, "0")})`}
          className={({ isActive }) =>
            clsx(
              "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors",
              isActive
                ? "bg-brand-soft text-ink font-semibold border-l-2 border-brand"
                : "text-ink-muted hover:bg-bg-raised hover:text-ink"
            )
          }
        >
          <Icon size={18} aria-hidden className="shrink-0" />
          <span className="hidden truncate xl:inline">{label}</span>
          {phase > 3 && (
            <span className="ml-auto hidden rounded bg-bg-raised px-1.5 py-0.2 text-[10px] text-ink-faint xl:inline">
              P{phase}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
