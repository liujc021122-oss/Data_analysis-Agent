import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Button } from "@/components/ui/Button";

describe("Button", () => {
  it("disables itself and exposes busy state while loading", () => {
    render(<Button loading>保存</Button>);

    const button = screen.getByRole("button", { name: "保存" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("keeps normal click behavior when enabled", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>继续</Button>);

    fireEvent.click(screen.getByRole("button", { name: "继续" }));

    expect(onClick).toHaveBeenCalledOnce();
  });
});
