import { useEffect, useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { triggerPlanNow } from "@/api/client";

interface CommandItem {
  id: string;
  title: string;
  category: "Navigation" | "Actions" | "Export";
  shortcut?: string;
  action: () => void;
}

export function CommandPalette() {
  const [isOpen, setIsOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [selectedIndex, setSelectedIndex] = useState(0);
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);

  const commands: CommandItem[] = [
    // Navigation
    {
      id: "nav-overview",
      title: "Go to Overview Dashboard",
      category: "Navigation",
      shortcut: "G O",
      action: () => navigate("/overview"),
    },
    {
      id: "nav-decisions",
      title: "Go to Recommendations & Decisions",
      category: "Navigation",
      shortcut: "G D",
      action: () => navigate("/decisions"),
    },
    {
      id: "nav-risk",
      title: "Go to Risk & Contingency Engine",
      category: "Navigation",
      shortcut: "G R",
      action: () => navigate("/risk"),
    },
    {
      id: "nav-network",
      title: "Go to Network Topology & Time-Travel Replay",
      category: "Navigation",
      shortcut: "G N",
      action: () => navigate("/network"),
    },
    {
      id: "nav-whatif",
      title: "Go to What-If Digital Twin Scenario Simulator",
      category: "Navigation",
      shortcut: "G W",
      action: () => navigate("/whatif"),
    },
    {
      id: "nav-alerts",
      title: "Go to System Alerts & Active Incidents",
      category: "Navigation",
      shortcut: "G A",
      action: () => navigate("/alerts"),
    },
    {
      id: "nav-health",
      title: "Go to System Health & SLO Error Budgets",
      category: "Navigation",
      shortcut: "G H",
      action: () => navigate("/health"),
    },
    {
      id: "nav-assistant",
      title: "Go to AI Operational Assistant",
      category: "Navigation",
      shortcut: "G S",
      action: () => navigate("/assistant"),
    },

    // Actions
    {
      id: "act-plan-now",
      title: "Trigger Decision Optimization Cycle Now",
      category: "Actions",
      action: async () => {
        try {
          await triggerPlanNow();
          alert("Decision cycle triggered successfully!");
        } catch (err: any) {
          alert(`Failed to trigger plan: ${err?.message}`);
        }
      },
    },
    {
      id: "act-test-notification",
      title: "Dispatch Synthetic P1 Escalation Alert (Mailpit)",
      category: "Actions",
      action: async () => {
        try {
          const res = await fetch("/api/notifications/test", { method: "POST" });
          const json = await res.json();
          alert(`P1 Alert sent to Mailpit! Status: ${json.status}`);
        } catch (err: any) {
          alert(`Failed to send alert: ${err?.message}`);
        }
      },
    },
  ];

  const filteredCommands = commands.filter((c) =>
    c.title.toLowerCase().includes(search.toLowerCase()) ||
    c.category.toLowerCase().includes(search.toLowerCase())
  );

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setIsOpen((prev) => !prev);
      } else if (e.key === "Escape" && isOpen) {
        e.preventDefault();
        setIsOpen(false);
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen]);

  useEffect(() => {
    if (isOpen) {
      setSearch("");
      setSelectedIndex(0);
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [isOpen]);

  const handleSelect = (cmd: CommandItem) => {
    setIsOpen(false);
    cmd.action();
  };

  const handleInputKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev + 1) % Math.max(1, filteredCommands.length));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelectedIndex((prev) => (prev - 1 + filteredCommands.length) % Math.max(1, filteredCommands.length));
    } else if (e.key === "Enter" && filteredCommands[selectedIndex]) {
      e.preventDefault();
      handleSelect(filteredCommands[selectedIndex]);
    }
  };

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-20 bg-black/60 backdrop-blur-sm"
      onClick={() => setIsOpen(false)}
      data-testid="command-palette-backdrop"
    >
      <div
        className="w-full max-w-xl overflow-hidden rounded-xl border border-border bg-card shadow-2xl animate-in fade-in zoom-in-95 duration-100"
        onClick={(e) => e.stopPropagation()}
        data-testid="command-palette-modal"
      >
        {/* Search header */}
        <div className="flex items-center border-b border-border px-4 py-3">
          <span className="text-muted-foreground mr-2 text-sm">🔍</span>
          <input
            ref={inputRef}
            type="text"
            placeholder="Type a command or search (e.g. Decisions, Risk, Plan Now)..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setSelectedIndex(0);
            }}
            onKeyDown={handleInputKeyDown}
            className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none"
          />
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground">
            ESC
          </kbd>
        </div>

        {/* Command list */}
        <div className="max-h-80 overflow-y-auto p-2">
          {filteredCommands.length === 0 ? (
            <div className="py-6 text-center text-xs text-muted-foreground">
              No matching commands found.
            </div>
          ) : (
            filteredCommands.map((cmd, idx) => (
              <div
                key={cmd.id}
                onClick={() => handleSelect(cmd)}
                className={`flex items-center justify-between rounded-lg px-3 py-2 text-xs cursor-pointer transition-colors ${
                  idx === selectedIndex
                    ? "bg-primary text-primary-foreground font-semibold"
                    : "text-foreground hover:bg-muted/30"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span
                    className={`rounded px-1.5 py-0.5 text-[10px] uppercase font-bold ${
                      idx === selectedIndex
                        ? "bg-primary-foreground/20 text-primary-foreground"
                        : "bg-muted text-muted-foreground"
                    }`}
                  >
                    {cmd.category}
                  </span>
                  <span>{cmd.title}</span>
                </div>
                {cmd.shortcut && (
                  <span className="text-[10px] font-mono opacity-70">{cmd.shortcut}</span>
                )}
              </div>
            ))
          )}
        </div>

        {/* Footer tips */}
        <div className="flex items-center justify-between border-t border-border bg-muted/20 px-4 py-2 text-[11px] text-muted-foreground">
          <div className="flex items-center gap-3">
            <span>↑↓ Navigate</span>
            <span>↵ Select</span>
            <span>ESC Close</span>
          </div>
          <span className="font-mono text-[10px]">Ctrl+K</span>
        </div>
      </div>
    </div>
  );
}
