import { Link } from 'react-router-dom'
import { fmtDateTime } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import OpenedClosedChart from '../components/dashboard/OpenedClosedChart'
import StageChart from '../components/dashboard/StageChart'
import ScenarioChart from '../components/dashboard/ScenarioChart'
import { casesHref } from '../components/dashboard/drill'
import { usePeople } from '../shell/people'

type Dash = {
  kpis: Record<string, number | null>
  funnel: { case_type: string; title: string; state: string; label: string; count: number }[]
  trend: { day: string; opened: number; closed: number }[]
  scenarios: { scenario: string; title: string; alerts: number; closed: number; escalated: number }[]
  recent_events: { ts: string; case_id: string; actor: string; actor_kind: string; kind: string }[]
}

export default function Overview() {
  const { data, error } = useApi<Dash>('/dashboard')
  const { can } = usePeople()
  if (!data) return <Loading error={error} />
  const k = data.kpis
  return (
    <>
      <div className="page-h"><h1>Program overview</h1><p>Live view of the financial-crime caseload across monitoring, sanctions and KYC. Click any figure, bar or stage to open the cases behind it.</p></div>
      {can('view_team') && <div className="toolbar"><div className="seg"><button aria-pressed>Today</button><Link to="/overview/report"><button aria-pressed={false}>Month by month</button></Link></div></div>}
      <div className="kpis">
        <Kpi label="Open cases" value={k.open_cases ?? 0} to="/cases" />
        <Kpi label="Deadline within 7 days" value={k.sla_at_risk ?? 0} sub={`${k.sla_breached} already past it`} bad={!!k.sla_breached} to={casesHref({ due: 'week' })} />
        <Kpi label="SARs filed" value={k.sars_filed ?? 0} to={can('submit_filings') || can('view_team') ? '/filings' : undefined} />
        <Kpi label="Average days to close" value={k.avg_days_to_close ?? '–'} to={casesHref({ status: 'closed' })} />
        <Kpi label="Human agreement with agent" value={k.agent_agreement == null ? '–' : `${Math.round(k.agent_agreement * 100)}%`} sub={`${k.agent_reviewed} closed cases`} />
        <Kpi label="Transactions monitored" value={(k.transactions ?? 0).toLocaleString()} sub={`${k.alerts} alerts`} />
      </div>
      <div className="cols-2">
        <Card title="Cases opened and closed, last 30 days" action={<span className="chart-hint">Click a bar for that day's cases</span>}><OpenedClosedChart data={data.trend} /></Card>
        <Card title="Scenario effectiveness" action={<span className="chart-hint">Click a scenario</span>}>
          <ScenarioChart rows={data.scenarios} />
          <p className="muted small">The figure at the end of each bar is the share of closed cases that ended in a filing or confirmed match. Low conversion flags a scenario to tune.</p>
        </Card>
      </div>
      <div className="cols-2">
        <Card title="Open work by stage" action={<span className="chart-hint">Click a stage</span>}><StageChart rows={data.funnel} /></Card>
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
