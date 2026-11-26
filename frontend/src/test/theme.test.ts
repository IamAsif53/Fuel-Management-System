import { describe, it, expect, beforeEach, afterEach } from "vitest";
import {
  applyTheme,
  readStoredTheme,
  setTheme,
  THEME_STORAGE_KEY,
  toggleTheme,
} from "@/theme";

describe("theme system", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
    document.documentElement.classList.remove("dark");
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("defaults to dawn when nothing stored", () => {
    expect(readStoredTheme()).toBe("dawn");
  });

  it("applyTheme sets data-theme and dark class for legacy only", () => {
    applyTheme("dawn");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dawn");
    expect(document.documentElement.classList.contains("dark")).toBe(false);

    applyTheme("legacy");
    expect(document.documentElement.getAttribute("data-theme")).toBe("legacy");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("setTheme persists and toggleTheme flips dawn <-> legacy", () => {
    setTheme("legacy");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("legacy");
    expect(toggleTheme("legacy")).toBe("dawn");
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dawn");
  });

  it("ignores invalid stored values", () => {
    localStorage.setItem(THEME_STORAGE_KEY, "neon-purple");
    expect(readStoredTheme()).toBe("dawn");
  });
});
