import { AlertCircle, RefreshCw } from "lucide-react";

interface ErrorStateProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = "Failed to load data",
  message = "A network or server error occurred while retrieving state.",
  onRetry,
}: ErrorStateProps) {
  return (
    <div
      data-testid="error-state"
      className="flex flex-col items-center justify-center rounded-lg border border-state-down/30 bg-bg-panel p-8 text-center"
    >
      <div className="mb-3 rounded-full bg-state-down/15 p-3 text-state-down">
        <AlertCircle size={28} aria-hidden />
      </div>
      <h3 className="mb-1 text-base font-semibold text-ink">{title}</h3>
      <p className="mb-4 max-w-md text-sm text-ink-muted">{message}</p>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex items-center gap-2 rounded-md bg-brand px-4 py-2 text-sm font-medium text-white transition hover:bg-brand/90"
        >
          <RefreshCw size={14} /> Retry
        </button>
      )}
    </div>
  );
}
