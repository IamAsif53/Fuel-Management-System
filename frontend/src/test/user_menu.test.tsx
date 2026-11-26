import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { UserMenu } from "@/components/layout/UserMenu";
import * as client from "@/api/client";

vi.mock("@/api/client", async () => {
  const actual = await vi.importActual<typeof import("@/api/client")>("@/api/client");
  return {
    ...actual,
    getAuthUser: vi.fn(() => null),
    subscribeAuth: vi.fn((cb: (u: client.AuthUser | null) => void) => {
      cb(null);
      return () => undefined;
    }),
    subscribeAuthModal: vi.fn(() => () => undefined),
    login: vi.fn(),
    logout: vi.fn(),
    switchDemoRole: vi.fn(),
    requestAuthModal: vi.fn(),
  };
});

describe("UserMenu role switching", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(client.getAuthUser).mockReturnValue(null);
    vi.mocked(client.subscribeAuth).mockImplementation((cb) => {
      cb(null);
      return () => undefined;
    });
  });

  it("shows Sign In when logged out and opens login modal", () => {
    render(<UserMenu />);
    expect(screen.getByTestId("login-btn")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("login-btn"));
    expect(screen.getByTestId("login-modal")).toBeInTheDocument();
    expect(screen.getByTestId("quick-operator")).toBeInTheDocument();
    expect(screen.getByTestId("quick-supervisor")).toBeInTheDocument();
  });

  it("switches to supervisor immediately via quick action", async () => {
    vi.mocked(client.switchDemoRole).mockResolvedValue({
      username: "supervisor",
      actor: "supervisor",
      role: "supervisor",
    });
    render(<UserMenu />);
    fireEvent.click(screen.getByTestId("login-btn"));
    fireEvent.click(screen.getByTestId("quick-supervisor"));
    await waitFor(() => {
      expect(client.switchDemoRole).toHaveBeenCalledWith("supervisor");
    });
  });

  it("shows identity and role switcher when signed in", () => {
    const signedIn = {
      username: "operator",
      actor: "operator",
      role: "operator" as const,
    };
    vi.mocked(client.getAuthUser).mockReturnValue(signedIn);
    vi.mocked(client.subscribeAuth).mockImplementation((cb) => {
      cb(signedIn);
      return () => undefined;
    });
    render(<UserMenu />);
    expect(screen.getByTestId("auth-identity")).toHaveTextContent("operator");
    expect(screen.getByTestId("switch-operator")).toBeInTheDocument();
    expect(screen.getByTestId("switch-supervisor")).toBeInTheDocument();
    expect(screen.getByTestId("logout-btn")).toBeInTheDocument();
  });
});
