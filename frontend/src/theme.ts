export type FsipTheme = "dawn" | "legacy";

export const THEME_STORAGE_KEY = "fsip-theme";
export const DEFAULT_THEME: FsipTheme = "dawn";

export function isFsipTheme(value: unknown): value is FsipTheme {
  return value === "dawn" || value === "legacy";
}

export function readStoredTheme(): FsipTheme {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (isFsipTheme(raw)) return raw;
  } catch {
    /* ignore */
  }
  return DEFAULT_THEME;
}

export function applyTheme(theme: FsipTheme): void {
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.classList.toggle("dark", theme === "legacy");
}

export function setTheme(theme: FsipTheme): void {
  applyTheme(theme);
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* ignore */
  }
}

export function toggleTheme(current: FsipTheme): FsipTheme {
  const next: FsipTheme = current === "dawn" ? "legacy" : "dawn";
  setTheme(next);
  return next;
}
