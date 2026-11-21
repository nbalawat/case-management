import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, daysUntil, hoursUntil } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Loading, Pill, Priority, Rec, Sla, StageClock } from '../components/ui'
import type { CaseRow } from './Cases'

type Me = { id: string; name: string; title: string; about: string; read_only: boolean; queues: { id: string; label: string }[]; permissions: string[]
  sees?: { everything: boolean; views: string[]; restricted: boolean; note: string } }
type Work = {
  persona: Me; mine: CaseRow[]
  queues: { id: string; label: string; cases: CaseRow[]; assigned: number }[]
  stats: { mine: number; available: number; breached: number; waiting: number }
}

function WorkTable({ rows, onClaim }: { rows: CaseRow[]; onClaim?: (id: string) => void }) {
  const nav = useNavigate()
  return (
    <div className="scroll"><table className="rows">
      <thead><tr><th>Priority</th><th>Customer</th><th>Case type</th><th>Stage</th><th>Stage clock</th><th>Deadline</th><th>Agent view</th>{onClaim && <th />}</tr></thead>
      <tbody>{rows.map(c => (
        <tr key={c.id} tabIndex={0} onClick={() => nav(`/cases/${c.id}`)} onKeyDown={e => e.key === 'Enter' && nav(`/cases/${c.id}`)}>
          <td><Priority p={c.priority} /></td>
          <td><b>{c.party_name}</b>{c.restricted && <Pill tone="critical">Restricted</Pill>}<div className="muted small">{c.id}</div></td>
          <td>{c.type_title}</td>
          <td>{c.state_label}{c.state_kind === 'wait' && <Pill tone="warning">waiting</Pill>}</td>
          <td><StageClock hours={hoursUntil(c.stage_due)} /></td>
          <td><Sla days={daysUntil(c.sla_due)} open={c.open} /></td>
          <td><Rec r={c.agent_recommendation} /></td>
          {onClaim && <td className="num"><button onClick={e => { e.stopPropagation(); onClaim(c.id) }}>Claim</button></td>}
        </tr>))}</tbody>
    </table></div>
  )
}

export default function MyWork() {
  const { data, error, reload } = useApi<Work>('/work')
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const { persona: me, stats } = data
  const claim = (id: string) => api.post(`/cases/${id}/claim`).then(() => { setMsg('Case claimed. It is now in your list.'); reload() }).catch(e => setMsg(e.message))
  const worksCases = me.permissions.includes('work')
  return (
    <>
      <div className="page-h">
        <h1>{me.name}</h1>
        <p>{me.title}. {me.about}</p>
        <div className="chips">{me.queues.map(q => <Pill key={q.id} tone="human">{q.label}</Pill>)}{me.read_only && <Pill tone="warning">Read-only</Pill>}</div>
        {me.sees && <p className="muted small sees">You can see: {me.sees.views.join('; ').toLowerCase().replaceAll('their own', 'your own').replaceAll('they own', 'you own')}{me.sees.restricted ? ', including restricted cases' : ''}.{me.sees.note && ' That always includes cases you own or have worked on.'}</p>}
      </div>
      {!worksCases ? (
        <Card title="This role does not work cases">
          <p>{me.read_only
            ? 'You can open every case and audit trail, and nothing you do here changes anything.'
            : 'Your work is configuration and data rather than casework.'}</p>
          <p className="links">
            <Link to="/cases">All cases</Link><Link to="/team">Team view</Link>
            {me.permissions.includes('publish_workflow') && <Link to="/workflows">Workflows</Link>}
            {me.permissions.includes('load_data') && <Link to="/onboarding">Data onboarding</Link>}
            <Link to="/access">Access model</Link>
          </p>
        </Card>
      ) : <>
        <div className="kpis">
          <Kpi label="Assigned to me" value={stats.mine} />
          <Kpi label="Past the stage clock" value={stats.breached} bad={stats.breached > 0} sub="of my cases" />
          <Kpi label="Waiting on someone else" value={stats.waiting} sub="information requested" />
          <Kpi label="Available to claim" value={stats.available} sub={`across ${me.queues.length} queue${me.queues.length === 1 ? '' : 's'}`} />
        </div>
        {msg && <p className="msg" role="status">{msg}</p>}
        <Card title={`My cases (${data.mine.length})`}>
          {data.mine.length === 0 ? <p className="empty">Nothing assigned to you. Claim a case from a queue below.</p> : <WorkTable rows={data.mine} />}
        </Card>
        {data.queues.map(q => (
          <Card key={q.id} title={`${q.label} · ${q.cases.length} unassigned`} action={<span className="muted small">{q.assigned} already assigned in this queue</span>}>
            {q.cases.length === 0 ? <p className="empty">Queue is clear.</p> : <WorkTable rows={q.cases} onClaim={claim} />}
          </Card>))}
      </>}
    </>
  )
}
