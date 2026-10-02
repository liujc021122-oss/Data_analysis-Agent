import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FileUpload } from "@/components/ui/FileUpload";

describe("FileUpload", () => {
  it("rejects non-CSV files and reports the validation message", () => {
    const onFileSelected = vi.fn();
    render(<FileUpload onFileSelected={onFileSelected} />);

    const file = new File(["hello"], "notes.txt", { type: "text/plain" });
    fireEvent.change(screen.getByLabelText("选择数据集文件"), { target: { files: [file] } });

    expect(screen.getByRole("alert")).toHaveTextContent("CSV");
    expect(onFileSelected).not.toHaveBeenCalled();
  });

  it("reports an accepted CSV file", () => {
    const onFileSelected = vi.fn();
    render(<FileUpload onFileSelected={onFileSelected} />);

    const file = new File(["name,value"], "sales.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("选择数据集文件"), { target: { files: [file] } });

    expect(onFileSelected).toHaveBeenCalledWith(file);
    expect(screen.getByRole("status")).toHaveTextContent("sales.csv");
  });

  it("rejects files above the configured size", () => {
    const onFileSelected = vi.fn();
    render(<FileUpload maxSizeBytes={3} onFileSelected={onFileSelected} />);

    const file = new File(["1234"], "sales.csv", { type: "text/csv" });
    fireEvent.change(screen.getByLabelText("选择数据集文件"), { target: { files: [file] } });

    expect(screen.getByRole("alert")).toHaveTextContent("过大");
    expect(onFileSelected).not.toHaveBeenCalled();
  });
});
