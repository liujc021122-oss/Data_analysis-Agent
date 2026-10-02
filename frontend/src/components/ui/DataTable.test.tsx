import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { DataTable } from "@/components/ui/DataTable";

interface Row {
  id: string;
  name: string;
}

const columns = [
  { key: "name", header: "名称", render: (row: Row) => row.name },
];

describe("DataTable", () => {
  it("renders semantic headers and row labels", () => {
    render(<DataTable<Row> columns={columns} rows={[{ id: "1", name: "sales.csv" }]} getRowKey={(row) => row.id} />);

    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "名称" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "sales.csv" })).toBeInTheDocument();
  });

  it("renders an empty message and handles row activation", () => {
    const onRowClick = vi.fn();
    const { rerender } = render(
      <DataTable<Row> columns={columns} rows={[]} getRowKey={(row) => row.id} emptyMessage="还没有上传数据" />,
    );
    expect(screen.getByText("还没有上传数据")).toBeInTheDocument();

    rerender(<DataTable<Row> columns={columns} rows={[{ id: "1", name: "sales.csv" }]} getRowKey={(row) => row.id} onRowClick={onRowClick} />);
    fireEvent.click(screen.getByRole("row", { name: "sales.csv" }));
    expect(onRowClick).toHaveBeenCalledWith({ id: "1", name: "sales.csv" });
  });
});
