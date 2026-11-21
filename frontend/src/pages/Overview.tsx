import { Link } from 'react-router-dom'
import { fmtDateTime } from '../api'
import { useApi } from '../useApi'
import { Card, Loading, Pill } from '../components/ui'
import TrendChart from '../components/TrendChart'
import { usePeople } from '../App'

type Dash = {
  kpis: Record<string, number | null>
  funnel: { case_type: string; title: string; state: string; label: string; count: number }[]
  trend: { day: string; opened: number; closed: number }[]
  scenarios: { scenario: string; title: string; alerts: number; closed: number; escalated: number }[]
  recent_events: { ts: string; case_id: string; actor: string; actor_kind: string; kind: string }[]
}

const Kpi = ({ label, value, sub, tone }: { label: string; value: string | number; sub?: string; tone?: string }) => (
  <div className="kpi"><span>{label}</span><b className={tone}>{value}</b>{sub && <small>{sub}</small>}</div>
)

export default function Overview() {
  const { data, error } = useApi<Dash>('/dashboard')
  const { can } = usePeople()
  if (!data) return <Loading error={error} />
  const k = data.kpis
  const maxF = Math.max(...data.funnel.map(f => f.count), 1)
  const groups = [...new Set(data.funnel.map(f => f.title))]
  return (
    <>
      <div className="page-h"><h1>Program overview</h1><p>Live view of the financial-crime caseload across monitoring, sanctions and KYC.</p></div>
      {can('view_team') && <div className="toolbar"><div className="seg"><button aria-pressed>Today</button><Link to="/overview/report"><button aria-pressed={false}>Month by month</button></Link></div></div>}
      <div className="kpis">
        <Kpi label="Open cases" value={k.open_cases ?? 0} />
        <Kpi label="SLA at risk (7 days)" value={k.sla_at_risk ?? 0} sub={`${k.sla_breached} overdue`} tone={k.sla_breached ? 'bad' : ''} />
        <Kpi label="SARs filed" value={k.sars_filed ?? 0} />
        <Kpi label="Average days to close" value={k.avg_days_to_close ?? '–'} />
        <Kpi label="Human agreement with agent" value={k.agent_agreement == null ? '–' : `${Math.round(k.agent_agreement * 100)}%`} sub={`${k.agent_reviewed} closed cases`} />
        <Kpi label="Transactions monitored" value={(k.transactions ?? 0).toLocaleString()} sub={`${k.alerts} alerts`} />
      </div>
      <div className="cols-2">
        <Card title="Cases opened and closed, last 30 days"><TrendChart data={data.trend} /></Card>
        <Card title="Open work by stage">
          {groups.map(g => (
            <div key={g} className="funnel">
              <h3>{g}</h3>
              {data.funnel.filter(f => f.title === g).map(f => (
                <div className="bar-row" key={f.state} title={`${f.label}: ${f.count}`}>
                  <span>{f.label}</span>
                  <div className="track"><div className="fill" style={{ width: `${(f.count / maxF) * 100}%` }} /></div>
                  <b>{f.count}</b>
                </div>
              ))}
            </div>
          ))}
        </Card>
      </div>
      <div className="cols-2">
        <Card title="Scenario effectiveness">
          <table><thead><tr><th>Scenario</th><th className="num">Alerts</th><th className="num">Closed</th><th className="num">Escalated</th><th className="num">Conversion</th></tr></thead>
            <tbody>{data.scenarios.map(s => (
              <tr key={s.scenario}><td>{s.title}</td><td className="num">{s.alerts}</td><td className="num">{s.closed}</td><td className="num">{s.escalated}</td>
                <td className="num">{s.closed ? `${Math.round((s.escalated / s.closed) * 100)}%` : '–'}</td></tr>))}</tbody></table>
          <p className="muted small">Conversion is the share of closed alerts that ended in a SAR or confirmed match. Low conversion flags a scenario to tune.</p>
        </Card>
        <Card title="Audit stream" action={<span className="muted small">append-only</span>}>
          <ul className="events">{data.recent_events.map((e, i) => (
            <li key={i}><Pill tone={e.actor_kind === 'agent' ? 'agent' : e.actor_kind === 'human' ? 'human' : 'neutral'}>{e.actor_kind}</Pill>
              <span><b>{e.kind}</b> by {e.actor} on <Link to={`/cases/${e.case_id}`}>{e.case_id}</Link></span>
              <time>{fmtDateTime(e.ts)}</time></li>))}</ul>
        </Card>
      </div>
    </>
  )
}
