import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'

type Hit = { id: string; title: string; detail: string; flags?: string[]; restricted?: boolean; case_id?: string | null; party_id?: string }
type Results = { query: string; total: number; groups: Record<string, Hit[]> }
const GROUPS: [string, string][] = [['customer', 'Customers'], ['counterparty', 'Counterparties'], ['case', 'Cases'], ['alert', 'Alerts'], ['transaction', 'Transactions']]

const target = (kind: string, h: Hit) =>
  kind === 'customer' ? `/customers/${h.id}` : kind === 'counterparty' ? `/counterparties/${h.id}` : kind === 'case' ? `/cases/${h.id}`
    : kind === 'alert' && h.case_id ? `/cases/${h.case_id}` : `/customers/${h.party_id}`

/** One box for everything a person may see: customers, counterparties under any spelling, cases, alerts and transactions by ID. */
export default function Search() {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<Results | null>(null)
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState(0)
  const box = useRef<HTMLInputElement>(null)
  const nav = useNavigate()
  useEffect(() => {
    if (q.trim().length < 2) { setRes(null); return }
    const t = setTimeout(() => api.get<Results>(`/search?q=${encodeURIComponent(q.trim())}`).then(r => { setRes(r); setAt(0) }).catch(() => setRes(null)), 180)
    return () => clearTimeout(t)
  }, [q])
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes((e.target as HTMLElement)?.tagName)
      if (e.key === '/' && !typing) { e.preventDefault(); box.current?.focus() }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [])
  const flat = res ? GROUPS.flatMap(([kind]) => (res.groups[kind] ?? []).map(h => ({ kind, h }))) : []
  const pick = (i: number) => { const x = flat[i]; if (!x) return; nav(target(x.kind, x.h)); setOpen(false); setQ(''); box.current?.blur() }
  const indexOf = (kind: string, h: Hit) => flat.findIndex(x => x.kind === kind && x.h.id === h.id)
  return (
    <div className="search" onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false) }}>
      <input ref={box} type="search" placeholder="Search  /" aria-label="Search customers, counterparties, cases" value={q}
        onChange={e => { setQ(e.target.value); setOpen(true) }} onFocus={() => setOpen(true)}
        onKeyDown={e => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setAt(a => Math.min(a + 1, flat.length - 1)) }
          else if (e.key === 'ArrowUp') { e.preventDefault(); setAt(a => Math.max(a - 1, 0)) }
          else if (e.key === 'Enter') pick(at)
          else if (e.key === 'Escape') { setOpen(false); box.current?.blur() }
        }} />
      {open && res && (
        <div className="search-pop" role="listbox">
          {res.total === 0 ? <p className="empty">Nothing you can see matches “{res.query}”.</p> : GROUPS.map(([kind, label]) => (res.groups[kind] ?? []).length > 0 && (
            <div key={kind}>
              <div className="search-g">{label}</div>
              {res.groups[kind].map(h => {
                const i = indexOf(kind, h)
                return (
                  <button key={`${kind}-${h.id}`} role="option" aria-selected={i === at} className={i === at ? 'on' : ''} onMouseEnter={() => setAt(i)} onClick={() => pick(i)}>
                    <b>{h.title}</b>
                    {h.flags?.filter(f => f !== 'common').map(f => <span key={f} className="pill serious">{f.replaceAll('_', ' ')}</span>)}
                    {h.restricted && <span className="pill critical">Restricted</span>}
                    <span className="muted small">{h.detail}</span>
                  </button>)
              })}
            </div>))}
        </div>)}
    </div>
  )
}
