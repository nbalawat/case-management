import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDateTime, words } from '../../api'
import { useApi } from '../../useApi'
import { useNameOf } from '../../shell/people'
import { Card, Pill } from '../ui'
import type { Facets } from './types'

type Event = { id: number; ts: string; case_id: string; actor: string; actor_kind: string; kind: string; payload: Record<string, unknown> }
type Result = { total: number; page: number; pages: number; events: Event[] }
const BLANK = { actor: '', kind: '', case_id: '', date_from: '', date_to: '', text: '' }
const detail = (p: Record<string, unknown>) => Object.entries(p).filter(([, v]) => v !== null && v !== '' && typeof v !== 'object').map(([k, v]) => `${words(k)}: ${v}`).join(' · ')

/** The audit trail across cases: who did what, when, on which case. Only cases the reader may open are searched. */
export default function AuditSearch({ facets }: { facets: Facets }) {
  const nameOf = useNameOf()
  const [f, setF] = useState(BLANK)
  const [asked, setAsked] = useState(BLANK)
  const [page, setPage] = useState(1)
  const qs = new URLSearchParams(Object.entries(asked).filter(([, v]) => v)).toString()
  const { data, error } = useApi<Result>(`/audit?${qs}${qs ? '&' : ''}page=${page}`, [qs, page])
  const go = () => { setAsked(f); setPage(1) }
  const set = (k: keyof typeof BLANK) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value })
  return (
    <Card title={`Audit search${data ? ` (${data.total.toLocaleString()} events)` : ''}`} action={<button onClick={() => api.download(`/audit/export.csv?${qs}`)}>Download these</button>}>
      <form className="inline wrap" onSubmit={e => { e.preventDefault(); go() }}>
        <select aria-label="Who" value={f.actor} onChange={set('actor')}><option value="">Anyone</option>{facets.actors.map(a => <option key={a.actor} value={a.actor}>{nameOf(a.actor)} ({a.count})</option>)}</select>
        <select aria-label="What" value={f.kind} onChange={set('kind')}><option value="">Any event</option>{facets.kinds.map(k => <option key={k.kind} value={k.kind}>{words(k.kind.replace('.', ' '))} ({k.count})</option>)}</select>
        <input type="search" aria-label="Case" placeholder="Case id" value={f.case_id} onChange={set('case_id')} />
        <label className="small">From <input type="date" value={f.date_from} onChange={set('date_from')} /></label>
        <label className="small">To <input type="date" value={f.date_to} onChange={set('date_to')} /></label>
        <input type="search" aria-label="Words" placeholder="Words in a note or reason" value={f.text} onChange={set('text')} />
        <button className="primary" type="submit">Search</button>
        {qs && <button type="button" onClick={() => { setF(BLANK); setAsked(BLANK); setPage(1) }}>Clear</button>}
      </form>
      {error && <p className="msg" role="alert">{error}</p>}
      {data && (data.events.length === 0 ? <p className="empty">Nothing matches.</p> : <>
        <div className="scroll"><table><thead><tr><th>When</th><th>Who</th><th>What</th><th>Case</th><th>Recorded with it</th></tr></thead>
          <tbody>{data.events.map(e => <tr key={e.id}>
            <td className="nowrap">{fmtDateTime(e.ts)}</td>
            <td><Pill tone={e.actor_kind === 'agent' ? 'agent' : e.actor_kind === 'human' ? 'human' : 'neutral'}>{e.actor_kind}</Pill> {nameOf(e.actor)}</td>
            <td><b>{words(e.kind.replace('.', ' '))}</b></td><td><Link to={`/cases/${e.case_id}`}>{e.case_id}</Link></td><td className="small">{detail(e.payload)}</td></tr>)}</tbody></table></div>
        <div className="inline"><button disabled={page <= 1} onClick={() => setPage(page - 1)}>Newer</button><span className="muted small">Page {data.page} of {data.pages}</span>
          <button disabled={page >= data.pages} onClick={() => setPage(page + 1)}>Older</button></div>
      </>)}
      <p className="muted small">The trail is append-only. You are searching the cases you can open; a restricted case's events appear only to people cleared for it.</p>
    </Card>
  )
}
