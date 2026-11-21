import type { ColumnDef, RowData } from '@tanstack/react-table'

declare module '@tanstack/react-table' {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface ColumnMeta<TData extends RowData, TValue> {
    /** Name in the column picker and the header. */
    label?: string
    align?: 'right'
    /** Hidden until the person turns it on. */
    hidden?: boolean
    /** Cannot be hidden (the column that identifies the row). */
    pinned?: boolean
    className?: string
  }
}

export type GridColumn<T> = ColumnDef<T, unknown>
export type Density = 'comfortable' | 'compact'
export type SortState = { id: string; desc: boolean }[]
