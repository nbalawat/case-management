import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { daysUntil, fmtDate, hoursUntil } from '../api'
import { useApi } from '../useApi'
import { useNameOf } from '../App'
import BulkBar from '../components/BulkBar'
import { Card, Loading, Pill, Priority, Rec, Sla, StageClock } from '../components/ui'

export type CaseRow = {
  id: string; case_type: string; type_title: string; state: string; state_label: string; open: boolean; priority: string
  owner: string | null; party_id: string; party_name: string; restricted?: boolean; created: string; sla_due: string | null; sla_clock: string | null
  disposition: string | null; agent_recommendation: string | null; alerts: number
  domain: string; state_kind: string; queue: string | null; queue_label: string; stage_due: string | null; stage_breached: boolean; parent_id: string | null
}
const ORDER = ['critical', 'high', 'medium', 'low']

export default function Cases() {
  const [status, setStatus] = useState('open')
  const [type, setType] = useState('all')
  const [q, setQ] = useState('')
  const { data, error, reload } = useApi<CaseRow[]>(`/cases?status=${status}`)
  const intake = useApi<{ counts: Record<string, number> }>('/alerts/intake').data
  const [picked, setPicked] = useState<string[]>([])
  const toggle = (id: string) => setPicked(p => (p.includes(id) ? p.filter(x => x !== id) : [...p, id]))
  const nav = useNavigate()
  const nameOf = useNameOf()
  const rows = useMemo(() => (data ?? [])
    .filter(c => type === 'all' || c.case_type === type)
    .filter(c => !q || `${c.party_name} ${c.id} ${c.party_id}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => ORDER.indexOf(a.priority) - ORDER.indexOf(b.priority) || (a.sla_due ?? '').localeCompare(b.sla_due ?? '')), [data, type, q])
  const types = [...new Map((data ?? []).map(c => [c.case_type, c.type_title])).entries()]
  return (
    <>
      <div className="page-h"><h1>Cases</h1><p>Every case across monitoring, sanctions, fraud, KYC and QA, ordered by priority and deadline.
        {intake && (intake.counts.hibernated ?? 0) > 0 && <> <Link to="/alerts">{intake.counts.hibernated} weak alert{intake.counts.hibernated > 1 ? 's are' : ' is'} waiting</Link> instead of opening a case.</>}</p></div>
      <Card>
        <div className="toolbar">
          <div className="seg" role="group" aria-label="Status">
            {['open', 'closed', 'all'].map(s => <button key={s} aria-pressed={status === s} onClick={() => setStatus(s)}>{s[0].toUpperCase() + s.slice(1)}</button>)}
          </div>
          <select value={type} onChange={e => setType(e.target.value)} aria-label="Case type">
            <option value="all">All case types</option>{types.map(([id, t]) => <option key={id} value={id}>{t}</option>)}
          </select>
          <input type="search" placeholder="Search customer or case ID" value={q} onChange={e => setQ(e.target.value)} />
          <span className="muted">{rows.length} cases</span>
        </div>
        {picked.length > 0 && <BulkBar ids={picked} onDone={() => { setPicked([]); reload() }} onClear={() => setPicked([])} />}
        {!data ? <Loading error={error} /> : rows.length === 0 ? <p className="empty">No cases match these filters.</p> : (
          <div className="scroll"><table className="rows">
            <thead><tr><th><input type="checkbox" aria-label="Select all open cases shown" checked={rows.some(c => c.open) && rows.filter(c => c.open).every(c => picked.includes(c.id))}
              onChange={e => setPicked(e.target.checked ? rows.filter(c => c.open).slice(0, 200).map(c => c.id) : [])} /></th><th>Priority</th><th>Customer</th><th>Case type</th><th>Stage</th><th>Stage clock</th><th className="num">Alerts</th><th>Agent view</th><th>Deadline</th><th>Opened</th><th>Queue and owner</th></tr></thead>
            <tbody>{rows.map(c => (
              <tr key={c.id} tabIndex={0} onClick={() => nav(`/cases/${c.id}`)} onKeyDown={e => e.key === 'Enter' && nav(`/cases/${c.id}`)}>
                <td onClick={e => e.stopPropagation()}>{c.open && <input type="checkbox" aria-label={`Select ${c.id}`} checked={picked.includes(c.id)} onChange={() => toggle(c.id)} />}</td>
                <td><Priority p={c.priority} /></td>
                <td><b>{c.party_name}</b>{c.restricted && <Pill tone="critical">Restricted</Pill>}<div className="muted small">{c.id}</div></td>
                <td>{c.type_title}</td><td>{c.state_label}{c.state_kind === 'wait' && <Pill tone="warning">waiting</Pill>}</td>
                <td>{c.open ? <StageClock hours={hoursUntil(c.stage_due)} /> : <span className="muted">–</span>}</td><td className="num">{c.alerts}</td>
                <td><Rec r={c.agent_recommendation} /></td>
                <td><Sla days={daysUntil(c.sla_due)} open={c.open} /></td>
                <td>{fmtDate(c.created)}</td><td>{c.open ? <>{c.owner ? nameOf(c.owner) : <span className="muted">Unassigned</span>}<div className="muted small">{c.queue_label}</div></> : <span className="muted">{c.disposition === 'merged' ? 'Merged' : 'Closed'}</span>}</td>
              </tr>))}</tbody>
          </table></div>)}
      </Card>
    </>
  )
}
