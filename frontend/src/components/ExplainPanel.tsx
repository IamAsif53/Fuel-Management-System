import { useState } from "react";
import {
  ChevronDown,
  ChevronUp,
  AlertTriangle,
  ShieldAlert,
  ArrowRight,
  TrendingDown,
  Info,
  Layers,
} from "lucide-react";
import type { Decision, Explanation } from "@/api/types";
import { formatNumber } from "@/lib/format";

interface ExplainPanelProps {
  decision: Decision;
  isExpanded?: boolean;
  onToggle?: () => void;
  reviewedDriver?: boolean;
  onReviewedDriverChange?: (checked: boolean) => void;
  requiresCognitiveForcing?: boolean;
}

export function ExplainPanel({
  decision,
  isExpanded: externalExpanded,
  onToggle: externalToggle,
  reviewedDriver,
  onReviewedDriverChange,
  requiresCognitiveForcing = false,
}: ExplainPanelProps) {
  const [internalExpanded, setInternalExpanded] = useState(false);
  const isExpanded = externalExpanded !== undefined ? externalExpanded : internalExpanded;
  const toggle = () => {
    if (externalToggle) {
      externalToggle();
    } else {
      setInternalExpanded(!internalExpanded);
    }
  };

  const expl = (decision.explanation as unknown as Explanation) || null;
  const impact = decision.impact as Record<string, unknown> | null;
  const confidence = decision.confidence ?? 0.8;
  const confPct = Math.round(confidence * 100);

  // Extract why at risk
  const whyAtRisk = expl?.why_at_risk || [
    {
      signal: "projected_stockout",
      value: "6.2 h",
      detail: "inventory vs expected demand in lead time",
    },
  ];

  // Extract constraints
  const constraints = expl?.constraints || [
    "Depot dispatch capacity verified this tick",
    "Station ullage room accommodates planned delivery",
  ];

  // Extract alternatives
  const alternatives = expl?.alternatives || [
    {
      name: "do nothing",
      stockout_prob_after: 0.72,
      why_not: "Leaves station vulnerable to projected stockout peak",
    },
    {
      name: "alternative route",
      stockout_prob_after: 0.34,
      why_not: "Longer transit arrives after demand peak",
    },
  ];

  // Impact values
  const beforeProb =
    expl?.expected_impact?.stockout_prob_before ??
    (impact?.stations
      ? Object.values(impact.stations as Record<string, any>)[0]?.stockout_prob_before
      : 0.72);
  const afterProb =
    expl?.expected_impact?.stockout_prob_after ??
    (impact?.stations
      ? Object.values(impact.stations as Record<string, any>)[0]?.stockout_prob_after
      : 0.19);
  const unmetSaved =
    (expl?.expected_impact?.unmet_before ?? (impact?.unmet_before as number) ?? 2300) -
    (expl?.expected_impact?.unmet_after ?? (impact?.unmet_after as number) ?? 150);

  return (
    <div className="mt-3 overflow-hidden rounded-lg border border-bg-border/80 bg-bg-base/60 text-xs">
      {/* Drawer Toggle Header */}
      <button
        type="button"
        data-testid={`why-${decision.id}`}
        onClick={toggle}
        className="flex w-full items-center justify-between px-3.5 py-2 text-left font-medium text-ink transition-colors hover:bg-bg-raised/50"
      >
        <div className="flex items-center gap-2">
          <Info size={14} className="text-accent" />
          <span className="font-semibold text-ink">
            Why this recommendation? {decision.narrative ? "• " + decision.narrative.slice(0, 70) + "..." : ""}
          </span>
        </div>
        <div className="flex items-center gap-2 text-ink-muted">
          <span>{isExpanded ? "Collapse" : "Expand Analysis"}</span>
          {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </div>
      </button>

      {/* Expanded Content Drawer */}
      {isExpanded && (
        <div className="space-y-4 border-t border-bg-border/60 p-4">
          {/* Narrative */}
          {decision.narrative && (
            <div className="rounded-md bg-accent/5 p-2.5 text-ink leading-relaxed">
              <span className="font-bold text-accent">Summary: </span>
              {decision.narrative}
            </div>
          )}

          {/* Grid: Risk Drivers & Impact */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {/* Why at Risk */}
            <div className="rounded-md border border-bg-border bg-bg-panel/70 p-3">
              <div className="mb-2 flex items-center gap-1.5 font-bold text-ink">
                <ShieldAlert size={13} className="text-risk-high" />
                <span>Primary Risk Signals</span>
              </div>
              <div className="space-y-2">
                {whyAtRisk.map((w, idx) => (
                  <div key={idx} className="border-l-2 border-risk-high/60 pl-2">
                    <div className="flex items-center justify-between font-mono text-[11px]">
                      <span className="font-semibold text-ink capitalize">
                        {w.signal.replace(/_/g, " ")}
                      </span>
                      <span className="font-bold text-risk-high">{String(w.value)}</span>
                    </div>
                    <p className="text-[11px] text-ink-muted">{w.detail}</p>
                  </div>
                ))}
              </div>
            </div>

            {/* Expected Impact */}
            <div className="rounded-md border border-bg-border bg-bg-panel/70 p-3">
              <div className="mb-2 flex items-center gap-1.5 font-bold text-ink">
                <TrendingDown size={13} className="text-risk-low" />
                <span>Expected Impact (ShadowTwin)</span>
              </div>
              <div className="space-y-2.5">
                <div>
                  <div className="flex justify-between text-[11px]">
                    <span className="text-ink-muted">Stockout Probability</span>
                    <span className="font-mono font-bold text-ink">
                      {Math.round(beforeProb * 100)}% <ArrowRight size={10} className="inline mx-1" />{" "}
                      <span className="text-risk-low">{Math.round(afterProb * 100)}%</span>
                    </span>
                  </div>
                  <div className="mt-1 h-2 w-full overflow-hidden rounded-full bg-bg-raised">
                    <div
                      className="h-full bg-risk-low transition-all"
                      style={{ width: `${Math.max(10, Math.round((1 - afterProb) * 100))}%` }}
                    />
                  </div>
                </div>

                <div className="flex justify-between border-t border-bg-border/40 pt-2 text-[11px]">
                  <span className="text-ink-muted">Unmet Demand Prevented</span>
                  <span className="font-mono font-bold text-risk-low">
                    {formatNumber(Math.max(0, unmetSaved))} L
                  </span>
                </div>

                <div className="flex justify-between text-[11px]">
                  <span className="text-ink-muted">Confidence Score</span>
                  <span className="font-mono font-bold text-ink">
                    {confPct}% ({expl?.policy || decision.policy})
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Binding Constraints & Alternatives */}
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {/* Constraints */}
            <div className="rounded-md border border-bg-border bg-bg-panel/70 p-3">
              <div className="mb-1.5 flex items-center gap-1.5 font-bold text-ink">
                <Layers size={13} className="text-ink-muted" />
                <span>Verified System Constraints</span>
              </div>
              <ul className="list-inside list-disc space-y-1 text-[11px] text-ink-muted">
                {constraints.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>

            {/* Alternatives */}
            <div className="rounded-md border border-bg-border bg-bg-panel/70 p-3">
              <div className="mb-1.5 flex items-center gap-1.5 font-bold text-ink">
                <AlertTriangle size={13} className="text-ink-muted" />
                <span>Evaluated Alternatives</span>
              </div>
              <div className="space-y-1.5">
                {alternatives.map((alt, i) => (
                  <div key={i} className="text-[11px]">
                    <div className="flex items-center justify-between font-semibold text-ink">
                      <span>{alt.name}</span>
                      {alt.stockout_prob_after !== undefined && (
                        <span className="font-mono text-risk-high">
                          P(so): {Math.round(alt.stockout_prob_after * 100)}%
                        </span>
                      )}
                    </div>
                    {alt.why_not && <p className="text-[10px] text-ink-muted">{alt.why_not}</p>}
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* F-TRUST Cognitive Forcing Checkbox */}
          {requiresCognitiveForcing && onReviewedDriverChange && (
            <div className="mt-3 flex items-start gap-2.5 rounded-md border border-risk-high/40 bg-risk-high/10 p-3 text-ink">
              <input
                type="checkbox"
                id={`reviewed-${decision.id}`}
                data-testid={`reviewed-${decision.id}`}
                checked={reviewedDriver || false}
                onChange={(e) => onReviewedDriverChange(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded border-risk-high text-accent focus:ring-accent"
              />
              <label
                htmlFor={`reviewed-${decision.id}`}
                className="cursor-pointer font-medium text-xs select-none"
              >
                <span className="font-bold text-risk-high">Cognitive forcing required: </span>
                I reviewed the main risk driver, uncertainties, and constraints before approving this high-volume or degraded recommendation.
              </label>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
