import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";
import clsx from "clsx";
import { readStoredTheme, setTheme, type FsipTheme } from "@/theme";

/**
 * Light = dawn design (new). Dark = previous classic console.
 * Both options stay visible so the look can be switched anytime.
 */
export function ThemeToggle() {
  const [theme, setThemeState] = useState<FsipTheme>(() =>
    typeof document !== "undefined" ? readStoredTheme() : "dawn"
  );

  useEffect(() => {
    setTheme(theme);
  }, [theme]);

  return (
    <div
      data-testid="theme-toggle"
      role="group"
      aria-label="Color mode"
      className="inline-flex items-center rounded-full border border-bg-border bg-bg-raised p-0.5"
    >
      <button
        type="button"
        data-testid="theme-light"
        aria-pressed={theme === "dawn"}
        aria-label="Light mode"
        title="Light (new design)"
        onClick={() => setThemeState("dawn")}
        className={clsx(
          "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors",
          theme === "dawn"
            ? "bg-brand text-ink-contrast shadow-sm"
            : "text-ink-muted hover:text-ink"
        )}
      >
        <Sun size={12} aria-hidden />
        <span>Light</span>
      </button>
      <button
        type="button"
        data-testid="theme-dark"
        aria-pressed={theme === "legacy"}
        aria-label="Dark mode"
        title="Dark (previous design)"
        onClick={() => setThemeState("legacy")}
        className={clsx(
          "inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold transition-colors",
          theme === "legacy"
            ? "bg-brand text-ink-contrast shadow-sm"
            : "text-ink-muted hover:text-ink"
        )}
      >
        <Moon size={12} aria-hidden />
        <span>Dark</span>
      </button>
    </div>
  );
}
