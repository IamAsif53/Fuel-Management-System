/**
 * Operator identity chip (no Sign-In).
 * Auth login UI was removed to match the participant brief §19/§26 —
 * human review is Approve/Reject, not a username/password gate.
 */
export function UserMenu() {
  return (
    <div
      className="flex items-center gap-1.5 rounded-md border border-bg-border bg-bg-raised px-2 py-1 text-xs font-medium text-ink-muted"
      data-testid="operator-identity"
      title="Local demo operator (no sign-in required)"
    >
      <span className="inline-block h-1.5 w-1.5 rounded-full bg-risk-low" aria-hidden />
      <span>Operator</span>
    </div>
  );
}
