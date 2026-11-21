import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmtDate, fmtDateTime, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../shell/people'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import PageTabs from '../components/layout/PageTabs'
import { BAND, OUTCOME, type Assessment, type Doc, type Trigger } from '../components/kyc/types'

type Due = { party_id: string; name: string; risk_rating: string; pep: boolean; last_review: string; reviewed_before: boolean; due: string; state: string; case_id: string | null }
type Desk = {
  schedule: { counts: { overdue: number; due_soon: number; in_review: number; customers: number }; rows: Due[]; more: number; cycle: Record<string, number> }
  triggers: (Trigger & { party_id: string; party_name: string })[]
  pending_overrides: (Assessment & { party_id: string; party_name: string })[]
  documents: (Doc & { party_name: string })[]
  structures: { party_id: string; party_name: string; version: number; recorded_at: string; beneficial_owners: string[]; unidentified: number; flags: { code: string; text: string }[] }[]
  can_start: boolean; can_approve: boolean
}
const STATE: Record<string, [string, string]> = { overdue: ['critical', 'Overdue'], due_soon: ['warning', 'Due soon'], in_review: ['human', 'In review'] }
const day = (iso: string) => new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
const Who = ({ id, name }: { id: string; name: string }) => <><Link to={`/customers/${id}`}><b>{name}</b></Link><div className="muted small">{id}</div></>

/** The KYC desk in tabs: who is due a review, decisions waiting for a second person, what changed, ownership and documents. */
export default function Kyc() {
  const { data, error, reload } = useApi<Desk>('/kyc')
  const { holder, persona } = usePeople()
  const nameOf = useNameOf()
  const nav = useNavigate()
  const [show, setShow] = useState('overdue')
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const c = data.schedule.counts
  const decide = (party: string, approve: boolean) => api.post(`/customers/${party}/risk/decision`, { approve, note: notes[party] ?? '' })
    .then(() => { setMsg(approve ? 'Approved. The lower rating is in force.' : 'Refused. The rating stands.'); reload() }).catch(e => setMsg(e.message))
  const start = (party: string) => api.post<{ case_id: string }>(`/customers/${party}/review`, {}).then(r => nav(`/cases/${r.case_id}`)).catch(e => setMsg(e.message))
  const rows = data.schedule.rows.filter(r => r.state === show)
  return (
    <>
      <div className="page-h"><h1>KYC</h1>
        <p>Customers are reviewed on a cycle set by their rating ({Object.entries(data.schedule.cycle).map(([band, m]) => `${band} every ${m} months`).join(', ')}), and sooner when something changes. A lower rating never takes effect on one person's word.</p></div>
      <div className="kpis">
        <Kpi label="Reviews overdue" value={c.overdue} sub={`of ${c.customers} customers`} bad={c.overdue > 0} to="/kyc" />
        <Kpi label="Due in the next 60 days" value={c.due_soon} />
        <Kpi label="In review now" value={c.in_review} />
        <Kpi label="Ratings waiting for approval" value={data.pending_overrides.length} bad={data.pending_overrides.length > 0} to="/kyc?tab=approvals" />
        <Kpi label="Documents expired or expiring" value={data.documents.length} bad={data.documents.some(d => d.expired)} to="/kyc?tab=documents" />
      </div>
      {msg && <p className="notice" role="status">{msg}</p>}
      <PageTabs label="KYC desk" tabs={[
        { id: 'reviews', label: 'Periodic reviews', count: c.overdue, alert: c.overdue > 0, content: <>
      <Card title="Periodic reviews" action={<span className="seg" role="group" aria-label="Which reviews">
        {Object.entries(STATE).map(([k, [, label]]) => <button key={k} aria-pressed={show === k} onClick={() => setShow(k)}>{label} ({c[k as keyof typeof c]})</button>)}</span>}>
        {!data.can_start && <p className="muted small">You can read the schedule. Starting a review needs someone who works cases.</p>}
        {rows.length === 0 ? <p className="empty">None.</p> : (
          <div className="scroll tall"><table><thead><tr><th>Customer</th><th>Rating</th><th>Last reviewed</th><th>Due</th><th /></tr></thead>
            <tbody>{rows.map(r => <tr key={r.party_id}>
              <td><Who id={r.party_id} name={r.name} /></td>
              <td><Pill tone={BAND[r.risk_rating]}>{r.risk_rating}</Pill>{r.pep && <Pill tone="serious">PEP</Pill>}</td>
              <td>{day(r.last_review)}{!r.reviewed_before && <div className="muted small">at onboarding; never reviewed since</div>}</td>
              <td><Pill tone={STATE[r.state][0]}>{STATE[r.state][1]}</Pill> {day(r.due)}</td>
              <td>{r.case_id ? <Link to={`/cases/${r.case_id}`}>{r.case_id}</Link> : data.can_start ? <button onClick={() => start(r.party_id)}>Start review</button> : null}</td>
            </tr>)}</tbody></table></div>)}
        {data.schedule.more > 0 && <p className="muted small">And {data.schedule.more} more, further from their date.</p>}
      </Card>
        </> },
        { id: 'approvals', label: 'Approvals', count: data.pending_overrides.length, alert: data.pending_overrides.length > 0, content: <>
      <Card title="Lower ratings waiting for a second person">
        {data.pending_overrides.length === 0 ? <p className="empty">Nothing is waiting.</p> : (
          <div className="scroll"><table><thead><tr><th>Customer</th><th>Model says</th><th>Proposed</th><th>Why</th><th>Decision</th></tr></thead>
            <tbody>{data.pending_overrides.map(a => {
              const o = a.override!, own = o.by === persona?.id
              return <tr key={a.id}>
                <td><Who id={a.party_id} name={a.party_name} /></td>
                <td><Pill tone={BAND[a.band]}>{a.band}</Pill><div className="muted small">score {a.score}</div></td>
                <td><Pill tone={BAND[o.band]}>{o.band}</Pill></td>
                <td><b>{o.reason}</b><div>“{o.note}”</div><div className="muted small">{nameOf(o.by)}, {fmtDateTime(o.at)}</div></td>
                <td>{data.can_approve && !own ? <div className="inline tight">
                  <input type="search" aria-label={`Note on ${a.party_name}`} placeholder="Note (needed to refuse)" value={notes[a.party_id] ?? ''} onChange={e => setNotes({ ...notes, [a.party_id]: e.target.value })} />
                  <button className="primary" onClick={() => decide(a.party_id, true)}>Approve</button>
                  <button disabled={!(notes[a.party_id] ?? '').trim()} onClick={() => decide(a.party_id, false)}>Refuse</button>
                </div> : <span className="muted small">{own ? 'You proposed this, so someone else decides.' : `${holder('approve_risk')} can decide.`}</span>}</td>
              </tr>
            })}</tbody></table></div>)}
      </Card>
        </> },
        { id: 'changes', label: 'Changes between reviews', count: data.triggers.length, content: <>
        <Card title="What changed between reviews">
          {data.triggers.length === 0 ? <p className="empty">No events yet.</p> : (
            <ul className="feed">{data.triggers.map(t => <li key={t.id}>
              <b>{t.label}</b> · <Link to={`/customers/${t.party_id}`}>{t.party_name}</Link> <span className="muted small">{fmtDate(t.at)}</span>
              <div>{t.detail}</div>
              <div className="small"><Pill tone={t.outcome === 'noted' ? 'neutral' : 'human'}>{OUTCOME[t.outcome] ?? words(t.outcome)}</Pill>{t.case_id && <Link to={`/cases/${t.case_id}`}>{t.case_id}</Link>}</div>
            </li>)}</ul>)}
        </Card>
        </> },
        { id: 'ownership', label: 'Ownership', count: data.structures.filter(x => x.flags.length).length, content: <>
          <Card title="Ownership structures on record">
            {data.structures.length === 0 ? <p className="empty">None recorded.</p> : (
              <table><thead><tr><th>Customer</th><th>Beneficial owners</th><th>Concerns</th></tr></thead>
                <tbody>{data.structures.map(s => <tr key={s.party_id}>
                  <td><Who id={s.party_id} name={s.party_name} /></td>
                  <td>{s.beneficial_owners.join(', ') || <span className="muted">None identified</span>}<div className="muted small">Version {s.version}, {fmtDate(s.recorded_at)}{s.unidentified > 0.005 ? ` · ${Number(s.unidentified.toFixed(1))}% untraced` : ''}</div></td>
                  <td>{s.flags.length === 0 ? <span className="muted">None</span> : s.flags.map(f => <span key={f.code} title={f.text}><Pill tone="serious">{words(f.code)}</Pill></span>)}</td>
                </tr>)}</tbody></table>)}
          </Card>
        </> },
        { id: 'documents', label: 'Documents', count: data.documents.length, alert: data.documents.some(d => d.expired), content: <>
          <Card title="Documents expired or about to">
            {data.documents.length === 0 ? <p className="empty">Nothing is running out.</p> : (
              <table><thead><tr><th>Customer</th><th>Document</th><th>Expiry</th></tr></thead>
                <tbody>{data.documents.map(d => <tr key={d.id}>
                  <td><Who id={d.party_id} name={d.party_name} /></td><td>{d.title || d.filename}<div className="muted small">{d.kind_label}</div></td>
                  <td><Pill tone={d.expired ? 'critical' : 'warning'}>{d.expired ? 'Expired' : 'Expires'} {day(d.expires!)}</Pill></td>
                </tr>)}</tbody></table>)}
          </Card>
        </> },
      ]} />
    </>
  )
}
