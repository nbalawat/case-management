import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, Moon, Sparkles, Sun, UserRound } from 'lucide-react'
import { api, type Persona } from '../api'
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '../components/kit/command'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../components/kit/dialog'
import { Kbd } from '../components/kit/kbd'
import type { Module } from './registry'

type Hit = { id: string; title: string; detail: string; flags?: string[]; restricted?: boolean; case_id?: string | null; party_id?: string }
type Results = { query: string; total: number; groups: Record<string, Hit[]> }
const KINDS: [string, string][] = [['case', 'Cases'], ['customer', 'Customers'], ['counterparty', 'Counterparties'], ['alert', 'Alerts'], ['transaction', 'Transactions']]

const target = (kind: string, h: Hit) =>
  kind === 'customer' ? `/customers/${h.id}` : kind === 'counterparty' ? `/counterparties/${h.id}` : kind === 'case' ? `/cases/${h.id}`
    : kind === 'alert' && h.case_id ? `/cases/${h.case_id}` : `/customers/${h.party_id}`

const matches = (q: string, ...text: (string | undefined)[]) => {
  const s = q.trim().toLowerCase()
  return !s || text.some(t => t?.toLowerCase().includes(s))
}

export default function CommandPalette({ open, onOpenChange, modules, people, onPerson, dark, onToggleTheme }: {
  open: boolean; onOpenChange: (o: boolean) => void; modules: Module[]
  people?: Persona[]; onPerson?: (id: string) => void; dark: boolean; onToggleTheme: () => void
}) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<Results | null>(null)
  const nav = useNavigate()
  const close = (o: boolean) => { if (!o) { setQ(''); setRes(null) } onOpenChange(o) }
  useEffect(() => {
    const term = q.trim()
    if (term.length < 2) return
    let live = true
    const t = setTimeout(() => api.get<Results>(`/search?q=${encodeURIComponent(term)}`).then(r => { if (live) setRes(r) }).catch(() => { if (live) setRes(null) }), 160)
    return () => { live = false; clearTimeout(t) }
  }, [q])
  const go = (path: string) => { close(false); nav(path) }
  const pages = useMemo(() => modules.filter(m => matches(q, m.label, m.description, ...(m.keywords ?? []))), [modules, q])
  const persons = useMemo(() => (people ?? []).filter(p => q.trim().length > 0 && matches(q, p.name, p.title)), [people, q])
  const row = (icon: ReactNode, title: ReactNode, detail?: ReactNode) => <>
    <span className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-bg-2 text-soft">{icon}</span>
    <span className="min-w-0 flex-1"><span className="block truncate">{title}</span>{detail && <span className="block truncate text-xs text-muted">{detail}</span>}</span>
  </>
  return (
    <Dialog open={open} onOpenChange={close}>
      <DialogContent aria-describedby="palette-help">
        <DialogTitle className="sr-only">Search, go to or ask</DialogTitle>
        <DialogDescription id="palette-help" className="sr-only">Type to find a page, a case, a customer or a transaction, or to ask a question.</DialogDescription>
        <Command shouldFilter={false} label="Command palette">
          <CommandInput value={q} onValueChange={setQ} placeholder="Search cases and customers, go to a page, or ask a question" autoFocus />
          <CommandList>
            <CommandEmpty>Nothing matches. Press Enter on “Ask” to ask it as a question.</CommandEmpty>
            {q.trim().length > 2 && (
              <CommandGroup heading="Ask">
                <CommandItem value={`ask ${q}`} onSelect={() => go(`/ask?q=${encodeURIComponent(q.trim())}`)}>
                  {row(<Sparkles size={15} className="text-agent" />, <>Ask: <b>{q.trim()}</b></>, 'Answered from live case data, with the cases behind it')}
                </CommandItem>
              </CommandGroup>)}
            {res && q.trim().length >= 2 && KINDS.map(([kind, label]) => (res.groups[kind] ?? []).length > 0 && (
              <CommandGroup key={kind} heading={label}>
                {res.groups[kind].map(h => (
                  <CommandItem key={`${kind}-${h.id}`} value={`${kind}-${h.id}`} onSelect={() => go(target(kind, h))}>
                    {row(<ArrowRight size={15} />, <>{h.title}{h.restricted && <span className="pill critical ml-2">Restricted</span>}</>, h.detail)}
                  </CommandItem>))}
              </CommandGroup>))}
            {pages.length > 0 && (
              <CommandGroup heading="Go to">
                {pages.map(m => {
                  const Icon = m.icon
                  return <CommandItem key={m.id} value={`page-${m.id}`} onSelect={() => go(m.path)}>{row(<Icon size={15} />, m.label, m.description)}</CommandItem>
                })}
              </CommandGroup>)}
            {persons.length > 0 && onPerson && (
              <CommandGroup heading="View as (demo)">
                {persons.map(p => <CommandItem key={p.id} value={`person-${p.id}`} onSelect={() => { close(false); onPerson(p.id) }}>{row(<UserRound size={15} />, p.name, p.title)}</CommandItem>)}
              </CommandGroup>)}
            {matches(q, 'theme', 'dark', 'light', 'appearance') && (
              <CommandGroup heading="Preferences">
                <CommandItem value="theme" onSelect={() => { onToggleTheme(); close(false) }}>
                  {row(dark ? <Sun size={15} /> : <Moon size={15} />, dark ? 'Switch to light theme' : 'Switch to dark theme')}
                </CommandItem>
              </CommandGroup>)}
          </CommandList>
          <div className="flex items-center gap-3 border-t border-line px-4 py-2 text-xs text-muted">
            <span><Kbd>↑</Kbd> <Kbd>↓</Kbd> to move</span><span><Kbd>Enter</Kbd> to open</span><span><Kbd>Esc</Kbd> to close</span>
          </div>
        </Command>
      </DialogContent>
    </Dialog>
  )
}
