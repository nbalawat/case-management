import { useApi } from '../useApi'
import { useNameOf } from '../shell/people'
import { Card, Kpi, Pill } from './ui'

type Report = {
  reviews: number; decided: number; passed: number; failed: number; average_score: number | null; pass_mark: number
  by_person: { person: string; reviews: number; average_score: number; failed: number }[]
  by_criterion: { code: string; label: string; met: number; partly: number; not_met: number }[]
  root_causes: { code: string; label: string; count: number }[]
  agent: { sampled: number; followed: number; followed_failed: number; overridden: number; overridden_failed: number }
}

/** What QA scorecards say: who needs coaching, which criteria fall short, why, and how the agent's advice held up. */
export default function QaReport() {
  const { data } = useApi<Report>('/qa/report')
  const nameOf = useNameOf()
  if (!data || data.reviews === 0) return null
  const most = Math.max(1, ...data.root_causes.map(c => c.count))
  return (
    <Card title="Quality assurance findings" action={<span className="muted small">{data.reviews} scored review{data.reviews > 1 ? 's' : ''} · pass mark {data.pass_mark}%</span>}>
      <div className="kpis tight">
        <Kpi label="Average score" value={`${data.average_score}%`} bad={(data.average_score ?? 100) < data.pass_mark} />
        <Kpi label="Passed" value={data.passed} sub={`of ${data.decided} decided`} />
        <Kpi label="Failed and returned" value={data.failed} bad={data.failed > 0} />
        <Kpi label="Agent advice under review" value={data.agent.sampled ? `${data.agent.followed} followed` : '–'}
          sub={data.agent.sampled ? `${data.agent.followed_failed} of those failed QA · ${data.agent.overridden} overridden, ${data.agent.overridden_failed} failed` : 'no sampled case had advice'} />
      </div>
      <div className="cols-2 qa-report">
        <div>
          <h3>By criterion</h3>
          <table>
            <thead><tr><th>Criterion</th><th className="num">Met</th><th className="num">Partly</th><th className="num">Not met</th></tr></thead>
            <tbody>{data.by_criterion.map(c => (
              <tr key={c.code}><td>{c.label}</td><td className="num">{c.met}</td><td className="num">{c.partly || ''}</td>
                <td className="num">{c.not_met ? <Pill tone="critical">{c.not_met}</Pill> : ''}</td></tr>))}</tbody>
          </table>
        </div>
        <div>
          <h3>Root causes of shortfalls</h3>
          {data.root_causes.length === 0 ? <p className="empty">Every criterion was met on every review.</p> : data.root_causes.map(c => (
            <div className="bar-row cause" key={c.code} title={`${c.label}: ${c.count}`}>
              <span>{c.label}</span><div className="track"><div className="fill" style={{ width: `${(c.count / most) * 100}%` }} /></div><b>{c.count}</b>
            </div>))}
          <h3>By the person who closed the case</h3>
          <table>
            <thead><tr><th>Person</th><th className="num">Reviews</th><th className="num">Average</th><th className="num">Failed</th></tr></thead>
            <tbody>{data.by_person.map(p => (
              <tr key={p.person}><td>{nameOf(p.person)}</td><td className="num">{p.reviews}</td>
                <td className="num">{p.average_score < data.pass_mark ? <Pill tone="critical">{p.average_score}%</Pill> : `${p.average_score}%`}</td><td className="num">{p.failed || ''}</td></tr>))}</tbody>
          </table>
        </div>
      </div>
    </Card>
  )
}
