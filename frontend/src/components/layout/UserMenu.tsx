import { useEffect, useState } from "react";
import { LogIn, LogOut, Shield, UserRound, X } from "lucide-react";
import clsx from "clsx";
import {
  getAuthUser,
  login,
  logout,
  requestAuthModal,
  subscribeAuth,
  subscribeAuthModal,
  switchDemoRole,
  type AuthRole,
  type AuthUser,
} from "@/api/client";

const DEMO_HINT =
  "Demo accounts: operator / operator · supervisor / supervisor · viewer / viewer";

export function UserMenu() {
  const [user, setUser] = useState<AuthUser | null>(() => getAuthUser());
  const [modalOpen, setModalOpen] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [switching, setSwitching] = useState<AuthRole | null>(null);

  useEffect(() => subscribeAuth(setUser), []);
  useEffect(() => subscribeAuthModal(() => setModalOpen(true)), []);

  const openModal = () => {
    setError(null);
    setUsername("");
    setPassword("");
    setModalOpen(true);
  };

  const closeModal = () => {
    if (busy) return;
    setModalOpen(false);
    setError(null);
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
      setModalOpen(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  };

  const handleSwitch = async (role: "operator" | "supervisor") => {
    setSwitching(role);
    setError(null);
    try {
      await switchDemoRole(role);
      setModalOpen(false);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Role switch failed");
      requestAuthModal();
    } finally {
      setSwitching(null);
    }
  };

  const handleLogout = async () => {
    setBusy(true);
    try {
      await logout();
    } finally {
      setBusy(false);
    }
  };

  const roleLabel = user?.role
    ? user.role.charAt(0).toUpperCase() + user.role.slice(1)
    : null;

  return (
    <>
      <div className="flex items-center gap-1.5" data-testid="user-menu">
        {user ? (
          <>
            <div
              className="hidden sm:flex items-center rounded-md border border-bg-border bg-bg-raised p-0.5"
              role="group"
              aria-label="Active role"
            >
              <button
                type="button"
                data-testid="switch-operator"
                disabled={!!switching}
                onClick={() => handleSwitch("operator")}
                className={clsx(
                  "rounded px-2 py-1 text-[11px] font-semibold transition-colors",
                  user.role === "operator"
                    ? "bg-brand/20 text-brand"
                    : "text-ink-muted hover:text-ink"
                )}
                title="Switch to Operator — approve/reject, control panel"
              >
                {switching === "operator" ? "…" : "Operator"}
              </button>
              <button
                type="button"
                data-testid="switch-supervisor"
                disabled={!!switching}
                onClick={() => handleSwitch("supervisor")}
                className={clsx(
                  "rounded px-2 py-1 text-[11px] font-semibold transition-colors",
                  user.role === "supervisor"
                    ? "bg-brand/20 text-brand"
                    : "text-ink-muted hover:text-ink"
                )}
                title="Switch to Supervisor — policy + four-eyes second approval"
              >
                {switching === "supervisor" ? "…" : "Supervisor"}
              </button>
            </div>
            <div
              className="flex items-center gap-1.5 rounded-md border border-bg-border bg-bg-raised px-2 py-1 text-xs font-medium text-ink"
              data-testid="auth-identity"
              title={`${user.username} (${user.role})`}
            >
              {user.role === "supervisor" ? (
                <Shield size={12} className="text-brand" aria-hidden />
              ) : (
                <UserRound size={12} className="text-ink-muted" aria-hidden />
              )}
              <span className="max-w-[7rem] truncate">{user.username}</span>
              <span className="text-ink-muted">· {roleLabel}</span>
            </div>
            <button
              type="button"
              data-testid="logout-btn"
              onClick={handleLogout}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded-md border border-bg-border px-2 py-1 text-xs font-medium text-ink-muted hover:bg-bg-raised hover:text-ink"
              title="Sign out"
            >
              <LogOut size={12} />
              <span className="hidden md:inline">Sign out</span>
            </button>
          </>
        ) : (
          <button
            type="button"
            data-testid="login-btn"
            onClick={openModal}
            className="inline-flex items-center gap-1.5 rounded-md border border-brand/40 bg-brand/15 px-2.5 py-1 text-xs font-semibold text-brand hover:bg-brand/25"
          >
            <LogIn size={13} />
            Sign In
          </button>
        )}
      </div>

      {modalOpen && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
          data-testid="login-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby="login-title"
        >
          <div className="w-full max-w-sm rounded-xl border border-bg-border bg-bg-panel p-5 shadow-2xl">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h2 id="login-title" className="text-base font-semibold text-ink">
                  Sign in
                </h2>
                <p className="mt-1 text-xs text-ink-muted">
                  Role powers apply immediately after sign-in or role switch.
                </p>
              </div>
              <button
                type="button"
                onClick={closeModal}
                className="rounded p-1 text-ink-muted hover:bg-bg-raised hover:text-ink"
                aria-label="Close"
              >
                <X size={16} />
              </button>
            </div>

            <div className="mt-4 grid grid-cols-2 gap-2">
              <button
                type="button"
                data-testid="quick-operator"
                disabled={!!switching || busy}
                onClick={() => handleSwitch("operator")}
                className="rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs font-semibold text-ink hover:border-brand/40"
              >
                {switching === "operator" ? "Signing in…" : "Enter as Operator"}
              </button>
              <button
                type="button"
                data-testid="quick-supervisor"
                disabled={!!switching || busy}
                onClick={() => handleSwitch("supervisor")}
                className="rounded-lg border border-bg-border bg-bg-raised px-3 py-2 text-xs font-semibold text-ink hover:border-brand/40"
              >
                {switching === "supervisor" ? "Signing in…" : "Enter as Supervisor"}
              </button>
            </div>

            <form className="mt-4 space-y-3" onSubmit={handleLogin}>
              <div>
                <label htmlFor="fsip-username" className="text-xs font-medium text-ink-muted">
                  Username
                </label>
                <input
                  id="fsip-username"
                  data-testid="login-username"
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="mt-1 w-full rounded-md border border-bg-border bg-bg-raised px-3 py-2 text-sm text-ink outline-none focus:border-brand"
                  required
                />
              </div>
              <div>
                <label htmlFor="fsip-password" className="text-xs font-medium text-ink-muted">
                  Password
                </label>
                <input
                  id="fsip-password"
                  data-testid="login-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="mt-1 w-full rounded-md border border-bg-border bg-bg-raised px-3 py-2 text-sm text-ink outline-none focus:border-brand"
                  required
                />
              </div>
              {error && (
                <p className="text-xs text-risk-critical" data-testid="login-error">
                  {error}
                </p>
              )}
              <p className="text-[11px] text-ink-muted">{DEMO_HINT}</p>
              <button
                type="submit"
                data-testid="login-submit"
                disabled={busy || !!switching}
                className="w-full rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-60"
              >
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
