/** Design tokens via CSS variables — themes: dawn (default) + legacy (classic dark). */
/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="legacy"]'],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "var(--color-bg)",
          panel: "var(--color-bg-panel)",
          raised: "var(--color-bg-raised)",
          border: "var(--color-bg-border)",
          base: "var(--color-bg)",
        },
        ink: {
          DEFAULT: "var(--color-ink)",
          muted: "var(--color-ink-muted)",
          faint: "var(--color-ink-faint)",
          contrast: "var(--color-ink-contrast)",
        },
        brand: { DEFAULT: "var(--color-brand)", soft: "var(--color-brand-soft)" },
        risk: {
          low: "var(--color-risk-low)",
          medium: "var(--color-risk-medium)",
          high: "var(--color-risk-high)",
          critical: "var(--color-risk-critical)",
        },
        fuel: {
          diesel: "var(--color-fuel-diesel)",
          petrol: "var(--color-fuel-petrol)",
          octane: "var(--color-fuel-octane)",
        },
        state: {
          healthy: "var(--color-state-healthy)",
          degraded: "var(--color-state-degraded)",
          down: "var(--color-state-down)",
          unknown: "var(--color-state-unknown)",
        },
        surface: "var(--color-surface)",
        panel: "var(--color-panel)",
        "panel-border": "var(--color-panel-border)",
        accent: "var(--color-accent)",
        "ink-contrast": "var(--color-ink-contrast)",
        base: "var(--color-bg)",
        card: "var(--color-card)",
        background: "var(--color-background)",
        border: "var(--color-border)",
        foreground: "var(--color-foreground)",
        "muted-foreground": "var(--color-muted-foreground)",
        muted: "var(--color-muted)",
        primary: { DEFAULT: "var(--color-primary)", foreground: "var(--color-primary-foreground)" },
      },
      boxShadow: {
        panel: "var(--shadow-panel)",
      },
      fontFamily: {
        sans: ["Sora", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["Sora", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
      },
    },
  },
  plugins: [],
};
