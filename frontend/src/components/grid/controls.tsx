import { useEffect, useState } from 'react'
import { BookmarkPlus, ChevronDown, ChevronLeft, ChevronRight, Search, Trash2, X } from 'lucide-react'
import { cn } from '../../lib/cn'
import { Button } from '../kit/button'
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from '../kit/dropdown-menu'
import type { SavedView } from './prefs'

/** A filter chip: shows what is chosen, opens a checklist with how many rows each choice would give. */
export function FacetFilter({ label, options, value, onChange }: {
  label: string; options: { value: string; label: string; count?: number }[]; value: string[]; onChange: (v: string[]) => void
}) {
  const active = value.length > 0
  const names = options.filter(o => value.includes(o.value)).map(o => o.label)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button type="button" className={cn('chip-btn', active && 'on')} aria-label={`Filter by ${label.toLowerCase()}`}>
          <span>{label}{active && <>: <b>{names.length > 2 ? `${names.length} chosen` : names.join(', ')}</b></>}</span>
          <ChevronDown size={14} aria-hidden />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[280px]">
        <DropdownMenuLabel>{label}</DropdownMenuLabel>
        {options.length === 0 && <div className="px-2.5 py-1.5 text-sm text-muted">Nothing to choose</div>}
        {options.map(o => (
          <DropdownMenuCheckboxItem key={o.value} checked={value.includes(o.value)} onSelect={e => e.preventDefault()}
            onCheckedChange={on => onChange(on ? [...value, o.value] : value.filter(v => v !== o.value))}>
            <span className="min-w-0 flex-1 truncate">{o.label}</span>
            {o.count !== undefined && <span className="text-xs tabular-nums text-muted">{o.count}</span>}
          </DropdownMenuCheckboxItem>))}
        {active && <><DropdownMenuSeparator /><DropdownMenuItem onSelect={() => onChange([])}><X size={14} /> Clear</DropdownMenuItem></>}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** One choice from a short list, as a segmented control. */
export function Segmented({ label, options, value, onChange }: { label: string; options: [string, string][]; value: string; onChange: (v: string) => void }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([k, l]) => <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)}>{l}</button>)}
    </div>
  )
}

/** Search that waits until typing pauses before it asks. */
export function SearchInput({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  const [text, setText] = useState(value)
  useEffect(() => { const t = setTimeout(() => { if (text !== value) onChange(text) }, 250); return () => clearTimeout(t) }, [text]) // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <label className="grid-search">
      <Search size={15} aria-hidden />
      <input type="search" value={text} onChange={e => setText(e.target.value)} placeholder={placeholder} aria-label={placeholder} />
    </label>
  )
}

export function Pager({ page, size, total, onPage, onSize, sizes = [25, 50, 100, 200] }: {
  page: number; size: number; total: number; onPage: (p: number) => void; onSize: (s: number) => void; sizes?: number[]
}) {
  const last = Math.max(1, Math.ceil(total / size))
  const from = total === 0 ? 0 : (page - 1) * size + 1
  const to = Math.min(total, page * size)
  return (
    <div className="pager">
      <span className="muted">{from.toLocaleString()}–{to.toLocaleString()} of <b>{total.toLocaleString()}</b></span>
      <label className="muted">Rows <select value={size} onChange={e => onSize(Number(e.target.value))} aria-label="Rows per page">
        {sizes.map(s => <option key={s} value={s}>{s}</option>)}</select></label>
      <Button size="sm" variant="ghost" aria-label="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}><ChevronLeft size={16} /></Button>
      <span className="muted tabular-nums">Page {page} of {last}</span>
      <Button size="sm" variant="ghost" aria-label="Next page" disabled={page >= last} onClick={() => onPage(page + 1)}><ChevronRight size={16} /></Button>
    </div>
  )
}

/** Named views: the built-in ones every person gets, and the ones they saved. A view is just the list's address. */
export function ViewsMenu({ builtIn, saved, current, onApply, onSave, onDelete }: {
  builtIn: SavedView[]; saved: SavedView[]; current: string; onApply: (v: SavedView) => void; onSave: (name: string) => void; onDelete: (name: string) => void
}) {
  const [naming, setNaming] = useState(false)
  const [name, setName] = useState('')
  const active = [...builtIn, ...saved].find(v => v.query === current)
  return (
    <div className="flex items-center gap-1.5">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button type="button" className="chip-btn view-btn" aria-label="Views"><span>View: <b>{active?.name ?? 'Custom'}</b></span><ChevronDown size={14} aria-hidden /></button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-[260px]">
          <DropdownMenuLabel>Views</DropdownMenuLabel>
          {builtIn.map(v => <DropdownMenuItem key={v.name} onSelect={() => onApply(v)}>{v.name}</DropdownMenuItem>)}
          {saved.length > 0 && <><DropdownMenuSeparator /><DropdownMenuLabel>Saved by you</DropdownMenuLabel></>}
          {saved.map(v => (
            <DropdownMenuItem key={v.name} onSelect={() => onApply(v)}>
              <span className="flex-1 truncate">{v.name}</span>
              <span role="button" tabIndex={-1} aria-label={`Delete view ${v.name}`} className="text-muted hover:text-crit"
                onClick={e => { e.stopPropagation(); e.preventDefault(); onDelete(v.name) }}><Trash2 size={14} /></span>
            </DropdownMenuItem>))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setNaming(true)}><BookmarkPlus size={15} /> Save current view…</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {naming && (
        <form className="flex items-center gap-1.5" onSubmit={e => { e.preventDefault(); if (name.trim()) { onSave(name.trim()); setName(''); setNaming(false) } }}>
          <input autoFocus className="view-name" value={name} onChange={e => setName(e.target.value)} placeholder="Name this view" aria-label="View name"
            onKeyDown={e => e.key === 'Escape' && setNaming(false)} />
          <Button size="sm" variant="primary" type="submit">Save</Button>
          <Button size="sm" variant="ghost" type="button" onClick={() => setNaming(false)}>Cancel</Button>
        </form>)}
    </div>
  )
}
