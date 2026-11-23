import { Construction } from "lucide-react";

interface PlaceholderPageProps {
  title: string;
  phase: number;
  description: string;
}

export function PlaceholderPage({ title, phase, description }: PlaceholderPageProps) {
  const phaseStr = String(phase).padStart(2, "0");

  return (
    <div className="flex h-full min-h-[400px] flex-col items-center justify-center rounded-xl border border-dashed border-bg-border bg-bg-panel/40 p-12 text-center">
      <div className="mb-4 rounded-2xl bg-bg-raised p-4 text-brand">
        <Construction size={36} aria-hidden />
      </div>
      <span className="mb-2 inline-flex items-center rounded-full bg-brand/10 px-3 py-1 text-xs font-semibold uppercase tracking-wider text-brand">
        Phase {phaseStr} Roadmap
      </span>
      <h1 className="mb-2 text-2xl font-bold tracking-tight text-ink">{title}</h1>
      <p className="max-w-md text-sm text-ink-muted leading-relaxed mb-6">
        {description}
      </p>
      <div className="rounded-lg border border-bg-border bg-bg-raised px-4 py-2 text-xs font-mono text-ink-faint">
        Planned for implementation in Phase {phaseStr}
      </div>
    </div>
  );
}
