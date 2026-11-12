"use client";

import { DataTable } from "@/components/assistant-ui/elements/data-table";
import copy from "@/data/chat/displays.json";
import type { TableDisplay } from "@/types/protocol";
import { fill } from "@/utils/text";

export function TableDisplayView({ display }: { display: TableDisplay }) {
  const columns = display.columns.map((col) => ({
    key: col.key,
    label: col.label,
    sortable: true,
  }));

  return (
    <div data-testid="display-table" className="flex w-full min-w-0 flex-col gap-1.5">
      <DataTable
        columns={columns}
        rows={display.rows}
        emptyMessage={copy.table.empty}
        className="max-w-full"
      />
      {display.truncated ? (
        <p className="text-muted-foreground font-mono text-xs">
          {fill(copy.truncated, { count: display.rows.length })}
        </p>
      ) : null}
    </div>
  );
}
