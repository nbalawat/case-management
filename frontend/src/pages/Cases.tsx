import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { daysUntil, fmtDate } from '../api'
import { useApi } from '../useApi'
import { Card, Loading, Priority, Rec, Sla } from '../components/ui'

export type CaseRow = {
  id: string; case_type: string; type_title: string; state: string; state_label: string; open: boolean; priority: string
  owner: string | null; party_id: string; party_name: string; created: string; sla_due: string | null; sla_clock: string | null
  disposition: string | null; agent_recommendation: string | null; alerts: number
}
const ORDER = ['critical', 'high', 'medium', 'low']

export default function Cases() {
  const [status, setStatus] = useState('open')
  const [type, setType] = useState('all')
  const [q, setQ] = useState('')
  const { data, error } = useApi<CaseRow[]>(`/cases?status=${status}`)
  const nav = useNavigate()
  const rows = useMemo(() => (data ?? [])
    .filter(c => type === 'all' || c.case_type === type)
    .filter(c => !q || `${c.party_name} ${c.id} ${c.party_id}`.toLowerCase().includes(q.toLowerCase()))
    .sort((a, b) => ORDER.indexOf(a.priority) - ORDER.indexOf(b.priority) || (a.sla_due ?? '').localeCompare(b.sla_due ?? '')), [data, type, q])
  const types = [...new Map((data ?? []).map(c => [c.case_type, c.type_title])).entries()]
  return (
    <>
      <div className="page-h"><h1>Cases</h1><p>One queue for monitoring, sanctions and KYC work, ordered by priority and deadline.</p></div>
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
        {!data ? <Loading error={error} /> : rows.length === 0 ? <p className="empty">No cases match these filters.</p> : (
          <div className="scroll"><table className="rows">
            <thead><tr><th>Priority</th><th>Customer</th><th>Case type</th><th>Stage</th><th className="num">Alerts</th><th>Agent view</th><th>Deadline</th><th>Opened</th><th>Owner</th></tr></thead>
            <tbody>{rows.map(c => (
              <tr key={c.id} tabIndex={0} onClick={() => nav(`/cases/${c.id}`)} onKeyDown={e => e.key === 'Enter' && nav(`/cases/${c.id}`)}>
                <td><Priority p={c.priority} /></td>
                <td><b>{c.party_name}</b><div className="muted small">{c.id}</div></td>
                <td>{c.type_title}</td><td>{c.state_label}</td><td className="num">{c.alerts}</td>
                <td><Rec r={c.agent_recommendation} /></td>
                <td><Sla days={daysUntil(c.sla_due)} open={c.open} /></td>
                <td>{fmtDate(c.created)}</td><td>{c.owner ?? <span className="muted">Unassigned</span>}</td>
              </tr>))}</tbody>
          </table></div>)}
      </Card>
    </>
  )
}
