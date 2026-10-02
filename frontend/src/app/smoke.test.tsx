import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

describe("frontend toolchain", () => {
  it("can render a React test component", () => {
    render(<div>workbench ready</div>);
    expect(screen.getByText("workbench ready")).toBeInTheDocument();
  });
});
