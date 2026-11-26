import { useEffect, useState } from "react";
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
import { api } from "@/api/client";

export interface NavItem {
  to: string;
  label: string;
  Icon: LucideIcon;
  demoOnly?: boolean;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/overview", label: "Overview", Icon: Gauge },
  { to: "/network", label: "Network", Icon: Network },
  { to: "/risk", label: "Risk", Icon: ShieldAlert },
  { to: "/recommendations", label: "Recommendations", Icon: Inbox },
  { to: "/decisions", label: "Decisions", Icon: History },
  { to: "/alerts", label: "Alerts", Icon: Bell },
  { to: "/whatif", label: "What-if", Icon: FlaskConical },
  { to: "/assistant", label: "Assistant", Icon: Bot },
  { to: "/health", label: "System Health", Icon: HeartPulse },
  { to: "/control", label: "Control (demo)", Icon: Settings2, demoOnly: true },
];

export function Sidebar() {
  const [demoControlsEnabled, setDemoControlsEnabled] = useState(true);

  useEffect(() => {
    let cancelled = false;
    api<{ demo_controls_enabled?: boolean }>("/control/status")
      .then((res) => {
        if (!cancelled) {
          setDemoControlsEnabled(res?.demo_controls_enabled !== false);
        }
      })
      .catch(() => {
        if (!cancelled) setDemoControlsEnabled(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const items = NAV_ITEMS.filter((item) => !item.demoOnly || demoControlsEnabled);

  return (
    <nav
      className="flex w-14 shrink-0 flex-col gap-0.5 border-r border-bg-border bg-bg-panel/95 p-2 backdrop-blur-sm transition-all xl:w-56 xl:px-3 xl:py-3"
      aria-label="Main Navigation"
      data-testid="sidebar-nav"
    >
      <div className="mb-2 hidden px-2 pt-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint xl:block">
        Operations
      </div>
      {items.map(({ to, label, Icon }) => (
        <NavLink
          key={to}
          to={to}
          title={label}
          data-testid={`nav-${to.replace("/", "") || "overview"}`}
          className={({ isActive }) =>
            clsx(
              "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
              isActive
                ? "bg-brand-soft text-brand font-semibold shadow-sm ring-1 ring-brand/20"
                : "text-ink-muted hover:bg-bg-raised hover:text-ink"
            )
          }
        >
          <Icon size={18} aria-hidden className="shrink-0" />
          <span className="hidden truncate xl:inline">{label}</span>
        </NavLink>
      ))}
    </nav>
  );
}
