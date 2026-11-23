import clsx from "clsx";

interface LoadingSkeletonProps {
  className?: string;
  count?: number;
}

export function LoadingSkeleton({ className, count = 1 }: LoadingSkeletonProps) {
  return (
    <div data-testid="loading-skeleton" className="space-y-3">
      {Array.from({ length: count }).map((_, idx) => (
        <div
          key={idx}
          className={clsx(
            "h-24 animate-pulse rounded-lg border border-bg-border bg-bg-panel/60",
            className
          )}
        />
      ))}
    </div>
  );
}
