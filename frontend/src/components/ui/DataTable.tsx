import type { KeyboardEvent, ReactNode } from "react";

export interface DataTableColumn<T> {
  key: string;
  header: ReactNode;
  render: (row: T) => ReactNode;
  className?: string;
}

export interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  getRowKey: (row: T) => string;
  onRowClick?: (row: T) => void;
  emptyMessage?: string;
}

function activateRow<T>(event: KeyboardEvent<HTMLTableRowElement>, row: T, onRowClick: (row: T) => void): void {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    onRowClick(row);
  }
}

export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  onRowClick,
  emptyMessage = "暂无数据",
}: DataTableProps<T>) {
  return (
    <div className="table-scroll" role="region" aria-label="数据表格" tabIndex={0}>
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th className={column.className} key={column.key} scope="col">{column.header}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td className="table-empty" colSpan={columns.length}>{emptyMessage}</td>
            </tr>
          ) : rows.map((row) => (
            <tr
              className={onRowClick ? "table-row-clickable" : undefined}
              key={getRowKey(row)}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={onRowClick ? (event) => activateRow(event, row, onRowClick) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
            >
              {columns.map((column) => <td className={column.className} key={column.key}>{column.render(row)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
