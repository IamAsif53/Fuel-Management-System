import "@testing-library/jest-dom/vitest";

// Mock ResizeObserver for Recharts in jsdom
if (typeof window !== "undefined" && !window.ResizeObserver) {
  window.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
