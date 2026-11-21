import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDateTime } from '../../api'
import { usePeople } from '../../shell/people'
import { useApi } from '../../useApi'
import { Card, Loading, Pill } from '../ui'

export type Grant = {
  id: string; person: string; kind: 'case' | 'customer'; target: string; reason: string; opened: string; ends: string; live: boolean
  closed: string | null; review: 'justified' | 'not_justified' | null; review_label: string; reviewed_by: string | null; review_note: string
}
type Listing = { mine: Grant[]; to_review: Grant[]; reviewed: Grant[]; can_review: boolean; can_open: boolean; hours: number | null }

const MIN_REASON = 20
const link = (g: Grant) => (g.kind === 'case' ? `/cases/${g.target}` : `/customers/${g.target}`)

const State = ({ g }: { g: Grant }) => g.live ? <Pill tone="critical">open until {fmtDateTime(g.ends)}</Pill>
  : <Pill tone="neutral">{g.closed ? `closed ${fmtDateTime(g.closed)}` : 'expired'}</Pill>

const Verdict = ({ g }: { g: Grant }) =>
  <Pill tone={g.review === 'justified' ? 'good' : g.review === 'not_justified' ? 'critical' : 'warning'}>{g.review_label}</Pill>

/**
 * Shown where a case or customer could not be found. "Not found" is also what someone sees when the record exists but
 * is outside what they may see, so people who hold break-glass can open it here, for a reason, for a few hours.
 */
export function BreakGlassPanel({ kind, target, error, onOpened }: { kind: 'case' | 'customer'; target: string; error: string; onOpened: () => void }) {
  const { data } = useApi<Listing>('/break-glass')
  const [reason, setReason] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const notFound = /not found/i.test(error)
  if (!notFound || !data?.can_open) return <Loading error={error} />
  const open = () => api.post<Grant>('/break-glass', { kind, target, reason })
    .then(() => { setMsg(null); onOpened() }).catch(e => setMsg(e.message))
  return (
    <Card title={`This ${kind} is not available to you`}>
      <p>Either there is no {kind} <code>{target}</code>, or it is outside what your roles let you see. If you need it now, you can break glass: it opens this one {kind} to you for {data.hours} hours.
        Every opening is written to the client's access record{kind === 'case' ? ' and to the case history' : ''}, and someone holding the certify-access permission reviews it afterwards.</p>
      <textarea rows={3} value={reason} onChange={e => setReason(e.target.value)} aria-label="Why you need it"
        placeholder="Why you need it. For example: the customer called the fraud line about a wire I am tracing in another case" />
      <div className="toolbar">
        <button className="primary" disabled={reason.trim().length < MIN_REASON} onClick={open}
          title={reason.trim().length < MIN_REASON ? `Say why, in at least ${MIN_REASON} characters` : ''}>Break glass and open</button>
        <span className="muted small">{Math.max(0, MIN_REASON - reason.trim().length) || 'Ready'}{reason.trim().length < MIN_REASON ? ' more characters needed' : ''}</span>
      </div>
      {msg && <p className="msg" role="status">{msg}</p>}
    </Card>
  )
}

/** On the Access page: the person's own openings, and for reviewers, everyone else's waiting for a verdict. */
export function BreakGlassCard({ hours, restricted }: { hours: number | null; restricted: boolean }) {
  const { data, error, reload } = useApi<Listing>('/break-glass')
  const { nameOf } = usePeople()
  const [note, setNote] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<string | null>(null)
  if (hours === null) return (
    <Card title="Break-glass access">
      <p className="muted">This client's policy has no <code>break_glass</code> section, so nobody can open a case or customer outside their roles. Add one, and grant the <code>break_glass</code> permission to the roles that may use it.</p>
    </Card>)
  if (!data) return <Card title="Break-glass access"><Loading error={error} /></Card>
  const decide = (g: Grant, outcome: 'justified' | 'not_justified') =>
    api.post(`/break-glass/${g.id}/review`, { outcome, note: note[g.id] ?? '' })
      .then(() => { setMsg(`${nameOf(g.person)}'s opening of ${g.kind} ${g.target}: ${outcome === 'justified' ? 'justified' : 'not justified, and closed'}.`); reload() })
      .catch(e => setMsg(e.message))
  const close = (g: Grant) => api.post(`/break-glass/${g.id}/close`, {}).then(() => { setMsg(`Closed. ${g.kind === 'case' ? 'The case' : 'The customer'} is out of your sight again.`); reload() })
    .catch(e => setMsg(e.message))
  return (
    <Card title="Break-glass access" action={<span className="muted small">Opens one case or customer for {hours} hours · {restricted ? 'restricted cases included' : 'never restricted cases'}</span>}>
      {msg && <p className="msg" role="status">{msg}</p>}
      {data.can_review && <>
        <h3>Waiting for review</h3>
        {data.to_review.length === 0 ? <p className="empty">Nothing to review.</p> :
          <table><thead><tr><th>Who</th><th>Opened</th><th>Why</th><th>State</th><th>Your verdict</th></tr></thead>
            <tbody>{data.to_review.map(g => (
              <tr key={g.id}><td><b>{nameOf(g.person)}</b></td>
                <td><Link to={link(g)}>{g.kind === 'customer' ? 'Customer ' : ''}{g.target}</Link><div className="muted small">{fmtDateTime(g.opened)}</div></td>
                <td className="small">{g.reason}</td><td><State g={g} /></td>
                <td><input type="search" placeholder="Note (needed if not justified)" value={note[g.id] ?? ''} aria-label={`Note on ${g.id}`}
                  onChange={e => setNote({ ...note, [g.id]: e.target.value })} />
                  <div className="toolbar"><button onClick={() => decide(g, 'justified')}>Justified</button>
                    <button onClick={() => decide(g, 'not_justified')}>Not justified</button></div></td></tr>))}</tbody></table>}
      </>}
      <h3>Your openings</h3>
      {data.mine.length === 0 ? <p className="empty">{data.can_open ? 'You have not broken glass. A case or customer outside your sight offers it where it would say "not found".' : 'Your roles do not allow breaking glass.'}</p> :
        <table><thead><tr><th>Opened</th><th>Why</th><th>State</th><th>Review</th><th /></tr></thead>
          <tbody>{data.mine.map(g => (
            <tr key={g.id}><td><Link to={link(g)}>{g.kind === 'customer' ? 'Customer ' : ''}{g.target}</Link><div className="muted small">{fmtDateTime(g.opened)}</div></td>
              <td className="small">{g.reason}</td><td><State g={g} /></td>
              <td><Verdict g={g} />{g.review_note && <div className="muted small">{g.review_note}</div>}</td>
              <td>{g.live && <button onClick={() => close(g)}>Close now</button>}</td></tr>))}</tbody></table>}
      {data.can_review && data.reviewed.length > 0 && <>
        <h3>Reviewed</h3>
        <table><thead><tr><th>Who</th><th>Opened</th><th>Verdict</th></tr></thead>
          <tbody>{data.reviewed.map(g => (
            <tr key={g.id}><td>{nameOf(g.person)}</td><td>{g.kind === 'customer' ? 'Customer ' : ''}{g.target}<div className="muted small">{g.reason}</div></td>
              <td><Verdict g={g} /> <span className="muted small">by {nameOf(g.reviewed_by ?? '')}</span>{g.review_note && <div className="muted small">{g.review_note}</div>}</td></tr>))}</tbody></table>
      </>}
    </Card>
  )
}
