import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, hoursUntil } from '../api'
import { useApi } from '../useApi'
import { useNameOf } from '../App'
import { Card, Kpi, Loading, Pill, Priority, StageClock } from '../components/ui'
import QaReport from '../components/QaReport'
import type { CaseRow } from './Cases'

type TeamData = {
  people: { id: string; name: string; title: string; queues: string[]; open: number; breached: number }[]
  queues: { id: string; label: string; open: number; unassigned: number; breached: number; oldest_hours: number | null; members: string[] }[]
  attention: CaseRow[]
  qa: { sampled: number; open: number; passed: number; failed: number }
  can_reassign: boolean; can_run_timers: boolean
}

export default function Team() {
  const { data, error, reload } = useApi<TeamData>('/team')
  const nameOf = useNameOf()
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return error
    ? <Card title="Team view"><p className="empty">{error}. Team leads, the MLRO, the Head of FIU, the admin and the auditor have this view.</p></Card>
    : <Loading />
  const max = Math.max(1, ...data.people.map(p => p.open))
  const members = (queue: string | null) => data.queues.find(q => q.id === queue)?.members ?? []
  const reassign = (id: string, owner: string) => api.post(`/cases/${id}/reassign`, { owner, reason: 'Workload balancing' })
    .then(() => { setMsg(`Reassigned to ${nameOf(owner)}.`); reload() }).catch(e => setMsg(e.message))
  const sweep = () => api.post<{ stage_breaches: number; requests_overdue: number; alerts_expired: number; kyc_triggers: number; holds_overdue: number; dispute_deadlines: number }>('/timers/run')
    .then(r => { setMsg(`Clock sweep done: ${r.stage_breaches} new stage breach(es), ${r.requests_overdue} request(s) newly overdue, ${r.alerts_expired} waiting alert(s) closed, ${r.kyc_triggers} customer event(s) found, ${r.holds_overdue} hold review(s) newly overdue, ${r.dispute_deadlines} dispute deadline(s) newly missed.`); reload() }).catch(e => setMsg(e.message))
  return (
    <>
      <div className="page-h"><h1>Team</h1><p>Who is carrying what, where work is stuck, and what quality assurance is finding.</p></div>
      <div className="kpis">
        <Kpi label="Open cases" value={data.queues.reduce((n, q) => n + q.open, 0)} />
        <Kpi label="Unassigned" value={data.queues.reduce((n, q) => n + q.unassigned, 0)} />
        <Kpi label="Past the stage clock" value={data.attention.length} bad={data.attention.length > 0} />
        <Kpi label="QA reviews" value={data.qa.sampled} sub={`${data.qa.passed} passed · ${data.qa.failed} failed · ${data.qa.open} open`} />
      </div>
      {msg && <p className="msg" role="status">{msg}</p>}
      <div className="cols-2">
        <Card title="Workload by person">
          {data.people.map(p => (
            <div className="bar-row wide" key={p.id} title={`${p.name}: ${p.open} open, ${p.breached} past the stage clock`}>
              <span><b>{p.name}</b><div className="muted small">{p.title}</div></span>
              <div className="track"><div className="fill" style={{ width: `${(p.open / max) * 100}%`, visibility: p.open ? 'visible' : 'hidden' }} /></div>
              <b>{p.open}</b>
              <span>{p.breached > 0 ? <Pill tone="critical">{p.breached} late</Pill> : null}</span>
            </div>))}
        </Card>
        <Card title="Queues">
          <table>
            <thead><tr><th>Queue</th><th className="num">Open</th><th className="num">Unassigned</th><th className="num">Late</th><th>Oldest unassigned</th><th>Members</th></tr></thead>
            <tbody>{data.queues.map(q => (
              <tr key={q.id}><td><b>{q.label}</b></td><td className="num">{q.open}</td><td className="num">{q.unassigned}</td>
                <td className="num">{q.breached > 0 ? <Pill tone="critical">{q.breached}</Pill> : 0}</td>
                <td>{q.oldest_hours === null ? <span className="muted">–</span> : q.oldest_hours >= 48 ? `${Math.round(q.oldest_hours / 24)} days` : `${q.oldest_hours} hours`}</td>
                <td className="small">{q.members.map(nameOf).join(', ')}</td></tr>))}</tbody>
          </table>
        </Card>
      </div>
      <QaReport />
      <Card title={`Needs attention (${data.attention.length})`}
        action={data.can_run_timers ? <button onClick={sweep}>Run clock sweep</button> : undefined}>
        {data.attention.length === 0 ? <p className="empty">No case is past its stage clock.</p> : (
          <div className="scroll"><table>
            <thead><tr><th>Priority</th><th>Customer</th><th>Case type</th><th>Stage</th><th>Stage clock</th><th>Queue</th><th>Owner</th>{data.can_reassign && <th>Reassign</th>}</tr></thead>
            <tbody>{data.attention.map(c => (
              <tr key={c.id}>
                <td><Priority p={c.priority} /></td>
                <td><Link to={`/cases/${c.id}`}><b>{c.party_name}</b></Link><div className="muted small">{c.id}</div></td>
                <td>{c.type_title}</td><td>{c.state_label}</td><td><StageClock hours={hoursUntil(c.stage_due)} /></td>
                <td>{c.queue_label}</td><td>{c.owner ? nameOf(c.owner) : <span className="muted">Unassigned</span>}</td>
                {data.can_reassign && <td>
                  <select value="" aria-label={`Reassign ${c.id}`} onChange={e => e.target.value && reassign(c.id, e.target.value)}>
                    <option value="">Assign to…</option>
                    {members(c.queue).filter(m => m !== c.owner).map(m => <option key={m} value={m}>{nameOf(m)}</option>)}
                  </select></td>}
              </tr>))}</tbody>
          </table></div>)}
        <p className="muted small">A stage clock breach raises the case priority and flags it here. On Temporal each open case holds a durable timer for its stage; without it the clocks are checked when work lists load.</p>
      </Card>
    </>
  )
}
