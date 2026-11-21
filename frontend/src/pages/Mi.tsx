import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtMoney, words } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Loading } from '../components/ui'
import MonthBars from '../components/MonthBars'

type Report = {
  period: string; partial: boolean; generated: string; periods: string[]
  headline: { alerts: number; opened: number; closed: number; backlog: number; median_days: number | null; in_time: number | null; productive: number
    filings_sent: number; filings_late: number; stage_breaches: number }
  trend: { month: string; alerts: number; opened: number; closed: number; backlog: number; filings: number }[]
  case_types: { case_type: string; title: string; opened: number; closed: number; backlog: number; productive: number; median_days: number | null; in_time: number | null }[]
  ageing: { label: string; count: number; overdue: number }[]
  scenarios: { scenario: string; title: string; alerts: number; auto_closed: number; waiting: number; decided_ever: number; productive_ever: number; productive_rate: number | null }[]
  outcomes: { case_type: string; title: string; disposition: string; count: number }[]
  filings: { kind: string; sent: number; late: number; accepted: number; rejected: number }[]
  people: { id: string; name: string; title: string; closed: number; median_days: number | null; productive: number; open_now: number; qa_reviewed: number; qa_passed: number }[]
  quality: { reviewed: number; passed: number; failed: number; pass_rate: number | null }
  agents: { advised: number; agreed: number; agreement: number | null; auto_closed: number }
  fraud: { disputes_closed: number; claimed: number; recovered: number; loss: number }
  sanctions: { decided: number; released: number; blocked: number; rejected: number }
  detection: { changes_live: number; lookbacks_open: number }
}
const pct = (v: number | null) => (v === null ? '–' : `${Math.round(v * 100)}%`)
const monthLabel = (m: string) => new Date(`${m}-15`).toLocaleDateString('en-US', { month: 'long', year: 'numeric' })

/** One month of the operation as a committee reads it, with the months before it and files to take away. */
export default function Mi() {
  const [period, setPeriod] = useState('')
  const { data, error } = useApi<Report>(`/mi${period ? `?period=${period}` : ''}`, [period])
  const [err, setErr] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const h = data.headline
  const get = (file: string) => api.download(`/mi/${file}?period=${data.period}`).catch(e => setErr(e.message))
  const oldest = Math.max(1, ...data.ageing.map(a => a.count))
  return (
    <>
      <div className="page-h"><h1>Management report</h1>
        <p>{monthLabel(data.period)}{data.partial ? ', so far' : ''}. Counted over every case at the client, whoever is reading: figures and workloads, never a case's contents.</p></div>
      <div className="toolbar">
        <div className="seg"><Link to="/overview"><button aria-pressed={false}>Today</button></Link><button aria-pressed>Month by month</button></div>
        <select aria-label="Month" value={data.period} onChange={e => setPeriod(e.target.value)}>{data.periods.map(p => <option key={p} value={p}>{monthLabel(p)}</option>)}</select>
        <button onClick={() => get('export.csv')}>Download as a spreadsheet</button><button onClick={() => get('pack.md')}>Download the committee paper</button>
        {err && <span className="msg" role="alert">{err}</span>}
      </div>
      <div className="kpis">
        <Kpi label="Alerts raised" value={h.alerts} />
        <Kpi label="Cases opened" value={h.opened} sub={`${h.closed} closed`} />
        <Kpi label="Open at the end" value={h.backlog} />
        <Kpi label="Median days to close" value={h.median_days ?? '–'} sub={`${pct(h.in_time)} inside their deadline`} />
        <Kpi label="Ended in a filing or confirmed concern" value={h.productive} sub={`of ${h.closed} closed`} />
        <Kpi label="Reports sent to regulators" value={h.filings_sent} sub={h.filings_late ? `${h.filings_late} after the due date` : 'none late'} bad={h.filings_late > 0} />
        <Kpi label="Stage deadlines missed" value={h.stage_breaches} bad={h.stage_breaches > 0} />
      </div>
      <div className="cols-2">
        <Card title="Cases opened and closed"><MonthBars data={data.trend} series={[{ key: 'opened', label: 'Opened', cls: 'in' }, { key: 'closed', label: 'Closed', cls: 'out' }]} /></Card>
        <Card title="Open at each month end"><MonthBars data={data.trend} series={[{ key: 'backlog', label: 'Open cases', cls: 'in' }]} /></Card>
      </div>
      <Card title="By kind of case">
        <div className="scroll"><table><thead><tr><th>Kind</th><th className="num">Opened</th><th className="num">Closed</th><th className="num">Open at the end</th><th className="num">Filing or confirmed concern</th><th className="num">Median days</th><th className="num">Inside deadline</th></tr></thead>
          <tbody>{data.case_types.map(t => <tr key={t.case_type}><td>{t.title}</td><td className="num">{t.opened}</td><td className="num">{t.closed}</td><td className="num">{t.backlog}</td>
            <td className="num">{t.productive}</td><td className="num">{t.median_days ?? '–'}</td><td className="num">{pct(t.in_time)}</td></tr>)}</tbody></table></div>
        {data.case_types.length === 0 && <p className="empty">No casework in this month.</p>}
      </Card>
      <div className="cols-2">
        <Card title="Open cases today, by age">
          <div className="net-key"><i className="k in" />Inside deadline<i className="k out" />Past deadline</div>
          {data.ageing.map(a => <div className="bar-row" key={a.label} title={`${a.label}: ${a.count} open, ${a.overdue} past deadline`}>
            <span>{a.label}</span><div className="rstack" style={{ width: `${(100 * a.count) / oldest}%` }}>{a.count - a.overdue > 0 && <i className="in" style={{ flex: a.count - a.overdue }} />}{a.overdue > 0 && <i className="out" style={{ flex: a.overdue }} />}</div><b>{a.count}</b></div>)}
          <p className="muted small">Counted as of now, not as of the end of the month.</p>
        </Card>
        <Card title="Detection">
          <table><thead><tr><th>Scenario</th><th className="num">Alerts this month</th><th className="num">Closed by an agent</th><th className="num">Led to a filing or concern, all time</th></tr></thead>
            <tbody>{data.scenarios.map(s => <tr key={s.scenario}><td>{s.title}</td><td className="num">{s.alerts}</td><td className="num">{s.auto_closed}</td>
              <td className="num">{pct(s.productive_rate)} <span className="muted small">of {s.decided_ever}</span></td></tr>)}</tbody></table>
          <p className="muted small">{data.detection.changes_live} change{data.detection.changes_live === 1 ? '' : 's'} to detection went live this month; {data.detection.lookbacks_open} lookback{data.detection.lookbacks_open === 1 ? ' is' : 's are'} in hand. See <Link to="/detection">Detection</Link>.</p>
        </Card>
      </div>
      <Card title="People">
        <div className="scroll"><table><thead><tr><th>Person</th><th className="num">Closed this month</th><th className="num">Median days</th><th className="num">Filing or confirmed concern</th><th className="num">Open now</th><th className="num">QA passed</th></tr></thead>
          <tbody>{data.people.map(p => <tr key={p.id}><td><b>{p.name}</b> <span className="muted small">{p.title}</span></td><td className="num">{p.closed}</td><td className="num">{p.median_days ?? '–'}</td>
            <td className="num">{p.productive}</td><td className="num">{p.open_now}</td><td className="num">{p.qa_reviewed ? `${p.qa_passed} of ${p.qa_reviewed}` : '–'}</td></tr>)}</tbody></table></div>
        {data.people.length === 0 && <p className="empty">Nobody closed or holds a case.</p>}
        <p className="muted small">A case is credited to the person who last moved it in the month. Speed alone says little: read it beside the outcomes and QA.</p>
      </Card>
      <div className="kpis">
        <Kpi label="Quality assurance" value={pct(data.quality.pass_rate)} sub={`${data.quality.passed} passed, ${data.quality.failed} failed`} />
        <Kpi label="People agreed with the agent" value={pct(data.agents.agreement)} sub={`on ${data.agents.advised} closed cases`} />
        <Kpi label="Fraud losses" value={fmtMoney(data.fraud.loss)} sub={`${fmtMoney(data.fraud.recovered)} recovered on ${data.fraud.disputes_closed} disputes`} />
        <Kpi label="Sanctions holds decided" value={data.sanctions.decided} sub={`${data.sanctions.blocked} blocked, ${data.sanctions.rejected} rejected, ${data.sanctions.released} released`} />
      </div>
      <div className="cols-2">
        <Card title="How cases ended">
          {data.outcomes.length === 0 ? <p className="empty">Nothing closed.</p> : <table><tbody>{data.outcomes.map(o => <tr key={o.case_type + o.disposition}><td>{o.title}</td><td>{words(o.disposition)}</td><td className="num">{o.count}</td></tr>)}</tbody></table>}
        </Card>
        <Card title="Reports to regulators">
          {data.filings.length === 0 ? <p className="empty">None sent.</p> : <table><thead><tr><th>Kind</th><th className="num">Sent</th><th className="num">Late</th><th className="num">Accepted</th><th className="num">Rejected</th></tr></thead>
            <tbody>{data.filings.map(f => <tr key={f.kind}><td>{f.kind}</td><td className="num">{f.sent}</td><td className="num">{f.late}</td><td className="num">{f.accepted}</td><td className="num">{f.rejected}</td></tr>)}</tbody></table>}
        </Card>
      </div>
    </>
  )
}
