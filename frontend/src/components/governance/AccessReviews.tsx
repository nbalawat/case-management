import { useState } from 'react'
import { api, fmtDate, words } from '../../api'
import { useNameOf } from '../../shell/people'
import { Card, Pill } from '../ui'
import type { Access, Item, Review } from './types'

const TONE: Record<string, string> = { keep: 'good', change: 'warning', remove: 'critical' }

function Row({ r, i, access, onChange }: { r: Review; i: Item; access: Access; onChange: (m: string) => void }) {
  const nameOf = useNameOf()
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const open = r.status === 'open' && access.can_certify && i.person !== access.me
  const decide = (decision: string) => api.post(`/governance/access-reviews/${r.id}/people/${i.person}`, { decision, note })
    .then(() => { setNote(''); setErr(null); onChange(`${i.name}: ${decision}.`) }).catch(e => setErr(e.message))
  return (
    <tr>
      <td><b>{i.name}</b><div className="muted small">{i.title}{i.read_only && ' · read-only'}</div></td>
      <td className="small">{i.permissions.map(words).join(', ') || 'None'}{i.queues.length > 0 && <div className="muted">Queues: {i.queues.join(', ')}</div>}</td>
      <td className="small">{i.last_active ? fmtDate(i.last_active) : '–'}<div className="muted">{i.open_cases} open</div></td>
      <td>{i.flags.map(f => <div key={f}><Pill tone="serious">{f}</Pill></div>)}</td>
      <td>{i.decision ? <><Pill tone={TONE[i.decision]}>{i.decision}</Pill>{i.done === true && <Pill tone="good">done</Pill>}{i.done === false && r.status === 'closed' && <Pill tone="warning">not yet made</Pill>}
        <div className="muted small">{i.note && `“${i.note}” `}{nameOf(i.decided_by!)}</div></> : r.status === 'open' && i.person === access.me ? <span className="muted small">Someone else decides yours</span> : <span className="muted">Not decided</span>}
        {open && <div className="inline tight"><input type="search" aria-label={`Note on ${i.name}`} placeholder={i.flags.length ? 'Why (needed)' : 'Note'} value={note} onChange={e => setNote(e.target.value)} />
          <button onClick={() => decide('keep')} disabled={i.flags.length > 0 && note.trim().length < 10}>Keep</button>
          <button onClick={() => decide('change')} disabled={note.trim().length < 10}>Change</button><button onClick={() => decide('remove')} disabled={note.trim().length < 10}>Remove</button></div>}
        {err && <p className="msg" role="alert">{err}</p>}</td>
    </tr>
  )
}

function One({ r, access, onChange }: { r: Review; access: Access; onChange: (m: string) => void }) {
  const nameOf = useNameOf()
  const [err, setErr] = useState<string | null>(null)
  return (
    <details className="wlist" open={r.status === 'open'}>
      <summary><b>{r.name}</b> <Pill tone={r.status === 'open' ? 'human' : 'neutral'}>{r.status === 'open' ? `Open: ${r.decided} of ${r.people} decided` : 'Closed'}</Pill>
        {r.overdue && <Pill tone="critical">Due {fmtDate(r.due)}</Pill>}
        {r.status === 'closed' && r.changes > 0 && <Pill tone={r.outstanding ? 'warning' : 'good'}>{r.outstanding ? `${r.outstanding} of ${r.changes} changes not yet made` : `${r.changes} changes made`}</Pill>}
        <span className="muted small"> started by {nameOf(r.started_by)}, {fmtDate(r.started)} · policy {r.policy_version}{r.closed_at && ` · closed by ${nameOf(r.closed_by!)}, ${fmtDate(r.closed_at)}`}</span></summary>
      <div className="scroll"><table><thead><tr><th>Person</th><th>Holds</th><th>Last case activity</th><th>For attention</th><th>Decision</th></tr></thead>
        <tbody>{r.items.map(i => <Row key={i.person} r={r} i={i} access={access} onChange={onChange} />)}</tbody></table></div>
      {r.status === 'open' && access.can_certify && <div className="inline"><button className="primary" disabled={r.decided < r.people}
        onClick={() => api.post(`/governance/access-reviews/${r.id}/close`).then(() => onChange('Review closed.')).catch(e => setErr(e.message))}>Close the review</button>
        <span className="muted small">A review records decisions. Changes are made on Access by whoever maintains the policy, and show here as done once they are.</span></div>}
      {err && <p className="msg" role="alert">{err}</p>}
    </details>
  )
}

/** Access recertification: everyone's access as it stood, a manager's decision on each, and whether the changes asked for were made. */
export default function AccessReviews({ access, onChange }: { access: Access; onChange: (m: string) => void }) {
  const [name, setName] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const start = () => api.post('/governance/access-reviews', { name }).then(() => { setName(''); setErr(null); onChange('Review started from the access policy in force.') }).catch(e => setErr(e.message))
  return (
    <Card title="Access recertification">
      {access.current && <div className="inline wrap">
        <span className="small">No review is open. Starting one today would cover {access.current.people} people, {access.current.flagged} of them flagged for attention.</span>
        {access.can_certify && <><input type="search" aria-label="Review name" placeholder="Name, such as the quarter it covers" value={name} onChange={e => setName(e.target.value)} />
          <button className="primary" disabled={name.trim().length < 5} onClick={start}>Start a review</button></>}</div>}
      {err && <p className="msg" role="alert">{err}</p>}
      {access.reviews.map(r => <One key={r.id} r={r} access={access} onChange={onChange} />)}
      <p className="muted small">Flags: holding both sides of a control, or no case activity in {access.dormant_days} days for someone who works cases. Nobody certifies their own access.</p>
    </Card>
  )
}
