/** Domain fuel SLO: target 99% service level; allowable unreliability 1%. */
export const DOMAIN_SLO_TARGET = 0.99;
export const DOMAIN_SLO_ALLOWABLE = 0.01;
/** Autopilot requires at least 25% of the error budget remaining. */
export const DOMAIN_SLO_AUTOPILOT_MIN_REMAINING = 0.25;

export interface DomainSloBudget {
  /** Fraction of error budget remaining (can be negative when below target). */
  remaining: number;
  /** Clamp remaining to [0, 1] for UI meters. */
  remainingClamped: number;
  required: number;
  exhausted: boolean;
  serviceLevel: number;
}

export function domainSloBudget(serviceLevel: number | null | undefined): DomainSloBudget {
  const sl = typeof serviceLevel === "number" && Number.isFinite(serviceLevel) ? serviceLevel : 1;
  const remaining = (sl - DOMAIN_SLO_TARGET) / DOMAIN_SLO_ALLOWABLE;
  return {
    remaining,
    remainingClamped: Math.max(0, Math.min(1, remaining)),
    required: DOMAIN_SLO_AUTOPILOT_MIN_REMAINING,
    exhausted: remaining < DOMAIN_SLO_AUTOPILOT_MIN_REMAINING,
    serviceLevel: sl,
  };
}
