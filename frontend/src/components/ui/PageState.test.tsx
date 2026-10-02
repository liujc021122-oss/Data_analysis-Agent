import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { PageState } from "@/components/ui/PageState";

describe("PageState", () => {
  it("announces an error and calls retry", () => {
    const retry = vi.fn();
    render(
      <PageState
        kind="error"
        title="数据暂时不可用"
        description="请检查 API 服务。"
        action={{ label: "重试", onClick: retry }}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("数据暂时不可用");
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(retry).toHaveBeenCalledOnce();
  });

  it("renders a helpful empty state", () => {
    render(<PageState kind="empty" title="还没有数据集" description="上传一个 CSV 文件开始分析。" />);

    expect(screen.getByText("还没有数据集")).toBeInTheDocument();
    expect(screen.getByText("上传一个 CSV 文件开始分析。" )).toBeInTheDocument();
  });
});
