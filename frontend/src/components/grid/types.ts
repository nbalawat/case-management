import {
  columnVisibilityFeature, createSortedRowModel, metaHelper, rowSortingFeature, sortFn_alphanumeric, sortFn_basic,
  sortFn_datetime, sortFn_text, tableFeatures, type ColumnDef, type RowData,
} from '@tanstack/react-table'

/** What a column can say about itself beyond its header and cell. */
export type GridColumnMeta = {
  /** Name in the column picker and the header. */
  label?: string
  align?: 'right'
  /** Hidden until the person turns it on. */
  hidden?: boolean
  /** Cannot be hidden (the column that identifies the row). */
  pinned?: boolean
  className?: string
}

/**
 * The table features every grid uses: sorting (client or server) and column choice. Registered once, outside any
 * component, so the table is stable between renders and only these features are bundled. The sort functions are the
 * ones automatic sorting picks from (dates, mixed text and numbers, text, anything else).
 */
export const gridFeatures = tableFeatures({
  rowSortingFeature,
  columnVisibilityFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns: { alphanumeric: sortFn_alphanumeric, basic: sortFn_basic, datetime: sortFn_datetime, text: sortFn_text },
  columnMeta: metaHelper<GridColumnMeta>(),
})
export type GridFeatures = typeof gridFeatures

export type GridColumn<T extends RowData> = ColumnDef<GridFeatures, T, unknown>
export type Density = 'comfortable' | 'compact'
export type SortState = { id: string; desc: boolean }[]
