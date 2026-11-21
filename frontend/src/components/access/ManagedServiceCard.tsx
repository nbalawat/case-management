import { useState } from 'react'
import { api, fmtDate, fmtDateTime } from '../../api'
import { useApi } from '../../useApi'
import { Card, Loading, Pill } from '../ui'

export type Engagement = {
  id: string; tenant: string; tenant_name: string; staff_id: string; person_id: string; name: string; title: string
  roles: { id: string; title: string }[]; queues: { id: string; label: string }[]
  status: 'proposed' | 'active' | 'rejected' | 'ended' | 'expired'; live: boolean; starts: string; ends: string; days_left: number
  note: string; proposed_by: string; proposed: string; decided_by: string | null; end_reason: string; ended: string | null
}
export type Allowance = {
  provider: string; roles: { id: string; title: string }[]; approval: 'client' | 'none'; max_days: number
  client_signoff: string[]; summary: string[]; queues: { id: string; label: string }[]
}
type View = {
  allows: Allowance | null; can_decide: boolean; engagements: Engagement[]
  log: { id: number; ts: string; actor: string; actor_name: string; org: string; kind: string; subject: string; detail: Record<string, unknown> }[]
}

const TONE: Record<Engagement['status'], string> = { proposed: 'warning', active: 'good', rejected: 'neutral', ended: 'neutral', expired: 'neutral' }
export const StatusPill = ({ e }: { e: Engagement }) =>
  <Pill tone={TONE[e.status]}>{e.status === 'proposed' ? 'waiting for approval' : e.status}</Pill>

const KIND: Record<string, string> = {
  'provider.request': 'Action', 'engagement.proposed': 'Engagement proposed', 'engagement.approved': 'Engagement approved',
  'engagement.rejected': 'Engagement turned down', 'engagement.ended': 'Engagement ended', 'engagement.withdrawn': 'Proposal withdrawn',
  'engagement.started': 'Engagement started',
}

/** The client's side of the managed service: its terms, who from the provider is here, approvals, and their record. */
export default function ManagedServiceCard() {
  const { data, error, reload } = useApi<View>('/access/managed-service')
  const [reason, setReason] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<string | null>(null)
  const [who, setWho] = useState('')
  if (!data) return <Card title="Managed-service team"><Loading error={error} /></Card>
  const act = (e: Engagement, verb: 'approve' | 'reject' | 'end') =>
    api.post(`/access/engagements/${e.id}/${verb}`, { reason: reason[e.id] ?? '' })
      .then(() => { setMsg(`${e.name}: ${verb === 'approve' ? 'approved. They can start now.' : verb === 'reject' ? 'turned down.' : 'engagement ended. Their cases are back in the queue.'}`); reload() })
      .catch(err => setMsg(err.message))
  const waiting = data.engagements.filter(e => e.status === 'proposed')
  const here = data.engagements.filter(e => e.live)
  const past = data.engagements.filter(e => !e.live && e.status !== 'proposed')
  const log = data.log.filter(l => !who || l.actor === who)
  const actors = [...new Map(data.log.filter(l => l.org === 'provider').map(l => [l.actor, l.actor_name])).entries()]
  if (!data.allows) return (
    <Card title="Managed-service team">
      <p className="muted">This client's access policy has no <code>managed_service</code> section, so nobody from the platform provider can be given a place here. Add one to the policy to let the provider's analysts work your queues on terms you set.</p>
    </Card>)
  const a = data.allows
  return (
    <Card title={`Managed-service team · ${a.provider}`} action={<span className="muted small">The provider proposes; {a.approval === 'client' ? 'you approve' : 'your policy waives approval'}</span>}>
      <ul className="legend-list">{a.summary.map(s => <li key={s}>{s}</li>)}</ul>
      {waiting.length > 0 && <>
        <h3>Waiting for your approval</h3>
        <table><thead><tr><th>Person</th><th>Roles and queues</th><th>Until</th><th>Why</th><th /></tr></thead>
          <tbody>{waiting.map(e => (
            <tr key={e.id}><td><b>{e.name}</b><div className="muted small">{e.title}</div></td>
              <td>{e.roles.map(r => <Pill key={r.id} tone="human">{r.title}</Pill>)}<div className="muted small">{e.queues.map(q => q.label).join(', ') || 'No queues'}</div></td>
              <td className="small nowrap">{fmtDate(e.ends)}</td><td className="small">{e.note || '–'}</td>
              <td className="nowrap">{data.can_decide ? <>
                <input type="search" placeholder="Note (optional)" aria-label={`Note on ${e.name}`} value={reason[e.id] ?? ''} onChange={ev => setReason({ ...reason, [e.id]: ev.target.value })} />
                <button className="primary" onClick={() => act(e, 'approve')}>Approve</button>
                <button className="ghost" onClick={() => act(e, 'reject')}>Turn down</button></> : <span className="muted small">Needs manage access</span>}</td></tr>))}</tbody></table>
      </>}
      <h3>Working here now</h3>
      {here.length === 0 ? <p className="empty">Nobody from {a.provider} is engaged here.</p> :
        <table><thead><tr><th>Person</th><th>Roles</th><th>Queues</th><th>Until</th><th /></tr></thead>
          <tbody>{here.map(e => (
            <tr key={e.id}><td><b>{e.name}</b> <Pill tone="serious">provider</Pill><div className="muted small">{e.title}</div></td>
              <td>{e.roles.map(r => <Pill key={r.id} tone="human">{r.title}</Pill>)}</td>
              <td className="small">{e.queues.map(q => q.label).join(', ') || <span className="muted">None</span>}</td>
              <td className="small nowrap">{fmtDate(e.ends)}<div className="muted">{e.days_left} days left</div></td>
              <td className="nowrap">{data.can_decide && <>
                <input type="search" placeholder="Why it ends" aria-label={`Why ${e.name}'s engagement ends`} value={reason[e.id] ?? ''} onChange={ev => setReason({ ...reason, [e.id]: ev.target.value })} />
                <button className="ghost" disabled={!(reason[e.id] ?? '').trim()} onClick={() => act(e, 'end')}>End now</button></>}</td></tr>))}</tbody></table>}
      {msg && <p className="msg" role="status">{msg}</p>}
      {past.length > 0 && <details><summary><b>Earlier engagements ({past.length})</b></summary>
        <table><tbody>{past.map(e => (
          <tr key={e.id}><td><b>{e.name}</b></td><td><StatusPill e={e} /></td><td className="small">{e.roles.map(r => r.title).join(', ')}</td>
            <td className="small">{e.end_reason}</td><td className="small nowrap">{e.ended ? fmtDateTime(e.ended) : ''}</td></tr>))}</tbody></table></details>}
      <h3>What the provider's staff did here</h3>
      <div className="toolbar">
        <select value={who} onChange={e => setWho(e.target.value)} aria-label="Whose actions"><option value="">Everyone</option>
          {actors.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select>
        <span className="muted small">Every page they open and every change they make, kept by this client. The provider cannot change it.</span>
      </div>
      <div className="scroll" style={{ maxHeight: 320 }}><table>
        <thead><tr><th>When</th><th>Who</th><th>What</th><th>Detail</th></tr></thead>
        <tbody>{log.slice(0, 120).map(l => (
          <tr key={l.id}><td className="small nowrap">{fmtDateTime(l.ts)}</td>
            <td className="small">{l.actor_name}{l.org === 'provider' && <Pill tone="serious">provider</Pill>}</td>
            <td className="small">{KIND[l.kind] ?? l.kind}</td><td className="small"><code>{l.subject}</code></td></tr>))}</tbody></table></div>
    </Card>
  )
}
