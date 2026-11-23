import { Inbox, type LucideIcon } from "lucide-react";

interface EmptyStateProps {
  title?: string;
  message?: string;
  icon?: LucideIcon;
}

export function EmptyState({
  title = "No data available",
  message = "There are currently no records to display.",
  icon: Icon = Inbox,
}: EmptyStateProps) {
  return (
    <div
      data-testid="empty-state"
      className="flex flex-col items-center justify-center rounded-lg border border-bg-border bg-bg-panel/50 p-8 text-center"
    >
      <div className="mb-3 rounded-full bg-bg-raised p-3 text-ink-faint">
        <Icon size={24} aria-hidden />
      </div>
      <h3 className="mb-1 text-sm font-semibold text-ink">{title}</h3>
      <p className="max-w-sm text-xs text-ink-muted">{message}</p>
    </div>
  );
}
