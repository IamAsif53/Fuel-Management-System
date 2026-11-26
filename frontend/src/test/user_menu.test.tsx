import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { UserMenu } from "@/components/layout/UserMenu";

describe("UserMenu operator identity", () => {
  it("shows Operator chip without Sign In controls", () => {
    render(<UserMenu />);
    expect(screen.getByTestId("operator-identity")).toBeInTheDocument();
    expect(screen.getByText("Operator")).toBeInTheDocument();
    expect(screen.queryByTestId("login-btn")).not.toBeInTheDocument();
    expect(screen.queryByTestId("login-modal")).not.toBeInTheDocument();
  });
});
