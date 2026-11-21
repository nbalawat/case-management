import { useState } from 'react'
import { api, fmtDate } from '../../api'
import { useApi } from '../../useApi'
import { Card, Kpi, Loading, Pill } from '../ui'
import { StatusPill, type Allowance, type Engagement } from '../access/ManagedServiceCard'

type Staff = { id: string; name: string; title: string; email: string; console: boolean; active: boolean; person_id: string; engagements: number }
type ClientTerms = { id: string; name: string; status: string; deployment: string; allows: Allowance | null; active: number; waiting: number; actions_7d: number }
type Overview = { staff: Staff[]; clients: ClientTerms[]; engagements: Engagement[] }

function Propose({ data, onDone }: { data: Overview; onDone: (msg: string) => void }) {
  const open = data.clients.filter(c => c.allows && c.status === 'live')
  const [tenant, setTenant] = useState(open[0]?.id ?? '')
  const [staff, setStaff] = useState('')
  const [roles, setRoles] = useState<string[]>([])
  const [queues, setQueues] = useState<string[]>([])
  const [days, setDays] = useState(90)
  const [note, setNote] = useState('')
  const [failed, setFailed] = useState<string | null>(null)
  const terms = open.find(c => c.id === tenant)?.allows
  if (!open.length) return <p className="empty">No client's policy allows the managed service yet.</p>
  const flip = (list: string[], set: (v: string[]) => void, v: string) => set(list.includes(v) ? list.filter(x => x !== v) : [...list, v])
  const submit = () => api.post<Engagement>('/platform/engagements', { tenant, staff_id: staff, roles, queues, days, note })
    .then(e => { setFailed(null); setRoles([]); setQueues([]); setNote(''); onDone(`${e.name} at ${e.tenant_name}: ${e.status === 'proposed' ? 'proposed. It waits for the client to approve.' : 'started.'}`) })
    .catch(err => setFailed(err.message))
  return (
    <div className="editor">
      <div>
        <div className="form">
          <label><span className="lbl">Client</span>
            <select value={tenant} onChange={e => { setTenant(e.target.value); setRoles([]); setQueues([]) }}>{open.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          <label><span className="lbl">Member of staff</span>
            <select value={staff} onChange={e => setStaff(e.target.value)}><option value="">Choose…</option>
              {data.staff.filter(m => m.active).map(m => <option key={m.id} value={m.id}>{m.name} · {m.title}</option>)}</select></label>
          <label><span className="lbl">Days (at most {terms?.max_days})</span>
            <input type="number" min={1} max={terms?.max_days} value={days} onChange={e => setDays(Number(e.target.value))} /></label>
          <label><span className="lbl">Why</span><input type="search" value={note} placeholder="Backlog cover, statement of work 12" onChange={e => setNote(e.target.value)} /></label>
        </div>
        <span className="lbl">Roles the client allows</span>
        <div className="checks">{terms?.roles.map(r => <label key={r.id} className="check"><input type="checkbox" checked={roles.includes(r.id)} onChange={() => flip(roles, setRoles, r.id)} />{r.title}</label>)}</div>
        <span className="lbl">Queues</span>
        <div className="checks">{terms?.queues.map(q => <label key={q.id} className="check"><input type="checkbox" checked={queues.includes(q.id)} onChange={() => flip(queues, setQueues, q.id)} />{q.label}</label>)}</div>
        <div className="toolbar"><button className="primary" disabled={!staff || !roles.length} onClick={submit}>Propose engagement</button></div>
        {failed && <p className="msg" role="alert">{failed}</p>}
      </div>
      <div>{terms && <div className="verdict good"><b>{open.find(c => c.id === tenant)?.name}'s terms</b><ul>{terms.summary.map(s => <li key={s}>{s}</li>)}</ul></div>}
        <p className="muted small">The client's own access policy sets these terms. Roles that control access are never offered to the provider, and the client can end an engagement at any time.</p></div>
    </div>
  )
}

/** The provider's side: who on the staff is working where, what each client allows, and new engagements. */
export default function ManagedServiceConsole() {
  const { data, error, reload } = useApi<Overview>('/platform/managed-service')
  const [msg, setMsg] = useState<string | null>(null)
  const [reason, setReason] = useState<Record<string, string>>({})
  const [adding, setAdding] = useState({ id: '', name: '', title: '' })
  if (!data) return <Card title="Managed service"><Loading error={error} /></Card>
  const done = (m: string) => { setMsg(m); reload() }
  const end = (e: Engagement) => api.post(`/platform/engagements/${e.tenant}/${e.id}/end`, { reason: reason[e.id] || 'Ended by the provider' })
    .then(() => done(`${e.name} no longer works at ${e.tenant_name}. Their cases went back to the queue.`)).catch(err => setMsg(err.message))
  const add = () => api.post('/platform/staff', adding).then(() => { setAdding({ id: '', name: '', title: '' }); done(`${adding.name} added to the staff directory.`) }).catch(err => setMsg(err.message))
  const current = data.engagements.filter(e => e.live || e.status === 'proposed')
  return (
    <>
      <div className="kpis">
        <Kpi label="Engaged now" value={data.engagements.filter(e => e.live).length} sub={`${data.staff.filter(s => s.engagements).length} people`} />
        <Kpi label="Waiting for a client" value={data.engagements.filter(e => e.status === 'proposed').length} />
        <Kpi label="Clients that allow it" value={data.clients.filter(c => c.allows).length} sub={`of ${data.clients.length}`} />
        <Kpi label="Actions at clients, 7 days" value={data.clients.reduce((n, c) => n + c.actions_7d, 0)} sub="each on that client's own record" />
      </div>
      <Card title="Engagements" action={<span className="muted small">Your staff at work in client queues, under each client's own access policy</span>}>
        <div className="scroll"><table>
          <thead><tr><th>Person</th><th>Client</th><th>Roles and queues</th><th>Status</th><th>Until</th><th /></tr></thead>
          <tbody>{current.map(e => (
            <tr key={e.id}><td><b>{e.name}</b><div className="muted small">{e.title}</div></td><td>{e.tenant_name}</td>
              <td>{e.roles.map(r => <Pill key={r.id} tone="human">{r.title}</Pill>)}<div className="muted small">{e.queues.map(q => q.label).join(', ') || 'No queues'}</div></td>
              <td><StatusPill e={e} /></td><td className="small nowrap">{fmtDate(e.ends)}</td>
              <td className="nowrap"><input type="search" placeholder="Why" aria-label={`Why ${e.name}'s engagement ends`} value={reason[e.id] ?? ''} onChange={ev => setReason({ ...reason, [e.id]: ev.target.value })} />
                <button className="ghost" onClick={() => end(e)}>{e.status === 'proposed' ? 'Withdraw' : 'End'}</button></td></tr>))}</tbody></table></div>
        {msg && <p className="msg" role="status">{msg}</p>}
      </Card>
      <Card title="Propose an engagement"><Propose data={data} onDone={done} /></Card>
      <div className="cols-2">
        <Card title="What each client allows">
          <table><thead><tr><th>Client</th><th>Terms</th></tr></thead>
            <tbody>{data.clients.map(c => (
              <tr key={c.id}><td><b>{c.name}</b><div className="muted small">{c.active} engaged · {c.waiting} waiting</div></td>
                <td className="small">{c.allows ? c.allows.summary.join('. ') : <span className="muted">Not offered. This client runs its own team.</span>}</td></tr>))}</tbody></table>
        </Card>
        <Card title="Staff directory">
          <table><thead><tr><th>Person</th><th>Engaged at</th></tr></thead>
            <tbody>{data.staff.map(m => (
              <tr key={m.id}><td><b>{m.name}</b>{m.console && <Pill tone="neutral">console</Pill>}<div className="muted small">{m.title}</div></td>
                <td className="small">{data.engagements.filter(e => e.staff_id === m.id && e.live).map(e => e.tenant_name).join(', ') || <span className="muted">Nowhere</span>}</td></tr>))}</tbody></table>
          <div className="toolbar">
            <input type="search" placeholder="id" aria-label="New staff id" value={adding.id} onChange={e => setAdding({ ...adding, id: e.target.value })} />
            <input type="search" placeholder="Full name" aria-label="New staff name" value={adding.name} onChange={e => setAdding({ ...adding, name: e.target.value })} />
            <input type="search" placeholder="Title" aria-label="New staff title" value={adding.title} onChange={e => setAdding({ ...adding, title: e.target.value })} />
            <button disabled={!adding.id || !adding.name} onClick={add}>Add</button>
          </div>
        </Card>
      </div>
    </>
  )
}
