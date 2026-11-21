import { useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import {
  flexRender, getCoreRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState, type VisibilityState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3, Rows3, Rows4 } from 'lucide-react'
import { cn } from '../../lib/cn'
import { Button } from '../kit/button'
import { DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuLabel, DropdownMenuTrigger } from '../kit/dropdown-menu'
import { load, save } from './prefs'
import type { Density, GridColumn, SortState } from './types'

const VIRTUAL_FROM = 120 // rows; below this every row is in the page, above it only what is on screen

type Props<T> = {
  /** Names the grid for saved preferences. */
  id: string
  label: string
  columns: GridColumn<T>[]
  rows: T[]
  getRowId: (row: T) => string
  /** Server-side sorting: the grid reports clicks and shows the state; the caller fetches. */
  sorting: SortState
  onSortingChange: (s: SortState) => void
  manualSorting?: boolean
  selectable?: (row: T) => boolean
  selected?: string[]
  onSelectedChange?: (ids: string[]) => void
  onOpen?: (row: T) => void
  onPreview?: (row: T) => void
  loading?: boolean
  empty?: ReactNode
  /** Filters and other controls on the left of the toolbar. */
  toolbar?: ReactNode
  /** Under the table: paging, counts. */
  footer?: ReactNode
  rowClassName?: (row: T) => string
}

/**
 * The one table every list uses. Sorting (client or server), column choice, density, selection, keyboard
 * movement (J and K or arrows, Enter to open, Space to preview, X to select) and row virtualisation for long lists.
 */
export default function DataGrid<T>({
  id, label, columns, rows, getRowId, sorting, onSortingChange, manualSorting, selectable, selected = [], onSelectedChange,
  onOpen, onPreview, loading, empty, toolbar, footer, rowClassName,
}: Props<T>) {
  const [visibility, setVisibility] = useState<VisibilityState>(() => load(id, 'columns',
    Object.fromEntries(columns.filter(c => c.meta?.hidden).map(c => [c.id ?? '', false]))))
  const [density, setDensity] = useState<Density>(() => load(id, 'density', 'comfortable'))
  const chosen = useMemo(() => new Set(selected), [selected])
  const pickable = useMemo(() => (selectable ? rows.filter(selectable) : []), [rows, selectable])

  const selectCol: ColumnDef<T, unknown> = {
    id: '__select', enableSorting: false, enableHiding: false, meta: { pinned: true, className: 'w-9' },
    header: () => <input type="checkbox" aria-label="Select all shown" checked={pickable.length > 0 && pickable.every(r => chosen.has(getRowId(r)))}
      onChange={e => onSelectedChange?.(e.target.checked ? pickable.map(getRowId) : [])} />,
    cell: ({ row }) => selectable?.(row.original) ? <input type="checkbox" aria-label={`Select ${getRowId(row.original)}`} checked={chosen.has(getRowId(row.original))}
      onClick={e => e.stopPropagation()} onChange={() => toggle(getRowId(row.original))} /> : null,
  }
  const toggle = (rid: string) => onSelectedChange?.(chosen.has(rid) ? selected.filter(x => x !== rid) : [...selected, rid])

  // TanStack Table hands back functions the React Compiler cannot memoise. Nothing here is memoised on them.
  // oxlint-disable-next-line react/incompatible-library
  const table = useReactTable<T>({
    data: rows, columns: selectable ? [selectCol, ...columns] : columns, getRowId,
    state: { sorting: sorting as SortingState, columnVisibility: visibility },
    onSortingChange: up => onSortingChange((typeof up === 'function' ? up(sorting as SortingState) : up) as SortState),
    onColumnVisibilityChange: up => setVisibility(v => { const next = typeof up === 'function' ? up(v) : up; save(id, 'columns', next); return next }),
    manualSorting, enableMultiSort: true, isMultiSortEvent: e => (e as MouseEvent).shiftKey,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: manualSorting ? undefined : getSortedRowModel(),
  })
  const model = table.getRowModel().rows

  const scroller = useRef<HTMLDivElement>(null)
  const virtual = model.length > VIRTUAL_FROM
  const rowHeight = density === 'compact' ? 36 : 52
  const virtualizer = useVirtualizer({ count: model.length, getScrollElement: () => scroller.current, estimateSize: () => rowHeight, overscan: 12, enabled: virtual })
  const items = virtual ? virtualizer.getVirtualItems() : null
  const shown = items ? items.map(i => model[i.index]) : model
  const padTop = items?.[0]?.start ?? 0
  const padBottom = items && items.length ? virtualizer.getTotalSize() - items[items.length - 1].end : 0

  const move = (e: KeyboardEvent<HTMLTableRowElement>, index: number) => {
    const row = model[index]?.original
    const go = (to: number) => {
      if (to < 0 || to >= model.length) return
      if (virtual) virtualizer.scrollToIndex(to)
      requestAnimationFrame(() => scroller.current?.querySelector<HTMLElement>(`tr[data-index="${to}"]`)?.focus())
    }
    if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); go(index + 1) }
    else if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); go(index - 1) }
    else if (e.key === 'Enter' && row) onOpen?.(row)
    else if (e.key === ' ' && row && onPreview) { e.preventDefault(); onPreview(row) }
    else if (e.key === 'x' && row && selectable?.(row)) toggle(getRowId(row))
  }
  const cols = table.getVisibleLeafColumns().length

  return (
    <div className={cn('grid-block', density === 'compact' && 'grid-compact')}>
      <div className="grid-toolbar">
        <div className="grid-filters">{toolbar}</div>
        <div className="grid-tools">
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button size="sm" aria-label="Choose columns"><Columns3 size={15} /> Columns</Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuLabel>Show columns</DropdownMenuLabel>
              {table.getAllLeafColumns().filter(c => c.getCanHide() && !c.columnDef.meta?.pinned).map(c => (
                <DropdownMenuCheckboxItem key={c.id} checked={c.getIsVisible()} onCheckedChange={v => c.toggleVisibility(!!v)} onSelect={e => e.preventDefault()}>
                  {c.columnDef.meta?.label ?? c.id}
                </DropdownMenuCheckboxItem>))}
            </DropdownMenuContent>
          </DropdownMenu>
          <Button size="sm" aria-label={density === 'compact' ? 'Comfortable rows' : 'Compact rows'} title={density === 'compact' ? 'Comfortable rows' : 'Compact rows'}
            onClick={() => { const d = density === 'compact' ? 'comfortable' : 'compact'; setDensity(d); save(id, 'density', d) }}>
            {density === 'compact' ? <Rows3 size={15} /> : <Rows4 size={15} />}
          </Button>
        </div>
      </div>
      <div ref={scroller} className={cn('grid-scroll', virtual && 'grid-virtual', loading && 'grid-loading')} aria-busy={loading}>
        <table className="data-grid" aria-label={label} aria-rowcount={model.length}>
          <thead>{table.getHeaderGroups().map(g => (
            <tr key={g.id}>{g.headers.map(h => {
              const meta = h.column.columnDef.meta
              const sorted = h.column.getIsSorted()
              return (
                <th key={h.id} className={cn(meta?.align === 'right' && 'num', meta?.className)}
                  aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : undefined}>
                  {h.isPlaceholder ? null : h.column.getCanSort()
                    ? <button type="button" className="sort-btn" onClick={h.column.getToggleSortingHandler()} title="Sort (Shift to add a second sort)">
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {sorted === 'asc' ? <ArrowUp size={13} /> : sorted === 'desc' ? <ArrowDown size={13} /> : <ArrowUpDown size={13} className="idle" />}
                      </button>
                    : flexRender(h.column.columnDef.header, h.getContext())}
                </th>)
            })}</tr>))}
          </thead>
          <tbody>
            {padTop > 0 && <tr aria-hidden style={{ height: padTop }}><td colSpan={cols} /></tr>}
            {shown.map(r => (
              <tr key={r.id} data-index={r.index} tabIndex={0} aria-selected={chosen.has(r.id) || undefined}
                className={cn(onOpen && 'clickable', rowClassName?.(r.original))}
                onClick={() => onOpen?.(r.original)} onKeyDown={e => move(e, r.index)}>
                {r.getVisibleCells().map(c => (
                  <td key={c.id} className={cn(c.column.columnDef.meta?.align === 'right' && 'num', c.column.columnDef.meta?.className)}>
                    {flexRender(c.column.columnDef.cell, c.getContext())}
                  </td>))}
              </tr>))}
            {padBottom > 0 && <tr aria-hidden style={{ height: padBottom }}><td colSpan={cols} /></tr>}
          </tbody>
        </table>
        {model.length === 0 && !loading && <div className="empty grid-empty">{empty ?? 'Nothing matches these filters.'}</div>}
      </div>
      {footer && <div className="grid-footer">{footer}</div>}
    </div>
  )
}
