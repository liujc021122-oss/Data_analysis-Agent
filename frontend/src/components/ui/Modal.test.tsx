import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Modal } from "@/components/ui/Modal";

describe("Modal", () => {
  it("closes when Escape is pressed while open", () => {
    const onClose = vi.fn();
    render(<Modal open title="确认删除" onClose={onClose}>确定要删除吗？</Modal>);

    expect(screen.getByRole("dialog", { name: "确认删除" })).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders nothing and ignores Escape while closed", () => {
    const onClose = vi.fn();
    render(<Modal open={false} title="隐藏" onClose={onClose}>内容</Modal>);

    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
  });
});
