import { useEffect, useState } from "react";
import clsx from "clsx";
import { CheckCircle2, AlertCircle, Info, X } from "lucide-react";

export interface ToastItem {
  id: string;
  type: "success" | "warning" | "info" | "error";
  title: string;
  message?: string;
}

export function ToastContainer() {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  useEffect(() => {
    const handleEvent = (e: Event) => {
      const custom = e as CustomEvent<{ type: string; data: Record<string, unknown> }>;
      const { type, data } = custom.detail || {};

      let toast: ToastItem | null = null;
      if (type === "decision.created") {
        toast = {
          id: `${Date.now()}-${Math.random()}`,
          type: "info",
          title: "New Decision Recommendation",
          message: `Decision #${data.decision_id || ""} generated at tick ${data.tick || ""}`,
        };
      } else if (type === "decision.approved") {
        toast = {
          id: `${Date.now()}-${Math.random()}`,
          type: "success",
          title: "Decision Approved",
          message: `Decision #${data.decision_id || ""} approved by ${data.approved_by || "operator"}`,
        };
      } else if (type === "decision.rejected") {
        toast = {
          id: `${Date.now()}-${Math.random()}`,
          type: "warning",
          title: "Decision Rejected",
          message: `Decision #${data.decision_id || ""} rejected: ${data.rejected_reason || ""}`,
        };
      } else if (type === "allocation.submitted") {
        toast = {
          id: `${Date.now()}-${Math.random()}`,
          type: "info",
          title: "Allocation Dispatched",
          message: `Allocation #${data.allocation_id || ""} en route to station`,
        };
      } else if (type === "allocation.delivered") {
        toast = {
          id: `${Date.now()}-${Math.random()}`,
          type: "success",
          title: "Shipment Delivered",
          message: `Delivered ${data.received_liters || data.quantity || ""}L to ${data.station_id || ""}`,
        };
      } else if (type === "allocation.failed") {
        toast = {
          id: `${Date.now()}-${Math.random()}`,
          type: "error",
          title: "Allocation Failed",
          message: `Dispatch failed: ${data.reason || "simulator error"}`,
        };
      } else if (type === "engine.mode_changed") {
        toast = {
          id: `${Date.now()}-${Math.random()}`,
          type: "info",
          title: "Engine Mode Changed",
          message: `Operating mode set to ${data.mode || ""}`,
        };
      }

      if (toast) {
        setToasts((prev) => [...prev.slice(-4), toast!]);
        setTimeout(() => {
          setToasts((prev) => prev.filter((t) => t.id !== toast!.id));
        }, 5000);
      }
    };

    window.addEventListener("fsip-event", handleEvent);
    return () => window.removeEventListener("fsip-event", handleEvent);
  }, []);

  const removeToast = (id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  if (toasts.length === 0) return null;

  return (
    <div
      aria-live="polite"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-sm pointer-events-none"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={clsx(
            "pointer-events-auto flex items-start gap-3 rounded-lg border p-3.5 shadow-lg backdrop-blur-md transition-all text-xs",
            t.type === "success" && "border-risk-low/30 bg-bg-panel/95 text-ink",
            t.type === "warning" && "border-risk-high/30 bg-bg-panel/95 text-ink",
            t.type === "error" && "border-risk-critical/30 bg-bg-panel/95 text-ink",
            t.type === "info" && "border-brand/30 bg-bg-panel/95 text-ink"
          )}
        >
          {t.type === "success" && <CheckCircle2 size={16} className="text-risk-low shrink-0 mt-0.5" />}
          {t.type === "warning" && <AlertCircle size={16} className="text-risk-high shrink-0 mt-0.5" />}
          {t.type === "error" && <AlertCircle size={16} className="text-risk-critical shrink-0 mt-0.5" />}
          {t.type === "info" && <Info size={16} className="text-brand shrink-0 mt-0.5" />}

          <div className="flex-1">
            <div className="font-semibold">{t.title}</div>
            {t.message && <div className="text-ink-muted mt-0.5 leading-relaxed">{t.message}</div>}
          </div>

          <button
            onClick={() => removeToast(t.id)}
            className="text-ink-faint hover:text-ink shrink-0 ml-1"
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
