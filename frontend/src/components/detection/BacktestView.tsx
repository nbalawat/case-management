import { Link } from 'react-router-dom'
import { Pill } from '../ui'
import { OUTCOME, type Backtest, type BacktestItem } from './types'

const TONE: Record<string, string> = { productive: 'critical', unproductive: 'neutral', undecided: 'warning', never_raised: 'neutral' }

function Items({ rows, more }: { rows: BacktestItem[]; more: number }) {
  return (
    <div className="scroll tall short"><table><thead><tr><th>Customer</th><th>What the alert said</th><th className="num">Score</th><th>What became of it</th></tr></thead>
      <tbody>{rows.map(r => <tr key={r.key}>
        <td><Link to={`/customers/${r.party_id}`}>{r.party_name}</Link></td><td className="small">{r.explanation}</td><td className="num">{r.score.toFixed(2)}</td>
        <td>{r.case_id ? <Link to={`/cases/${r.case_id}`}><Pill tone={TONE[r.outcome]}>{OUTCOME[r.outcome]}</Pill></Link> : <Pill tone={TONE[r.outcome]}>{OUTCOME[r.outcome]}</Pill>}</td>
      </tr>)}</tbody></table>
      {more > 0 && <p className="muted small">And {more} more.</p>}</div>
  )
}

/** What a change would have done to past alerts: how many go, how many of those mattered, and how many arrive. */
export default function BacktestView({ b }: { b: Backtest }) {
  const top = Math.max(1, b.alerts_now, b.alerts_then)
  const r = b.removed
  return (
    <div className="backtest">
      <div className="bt-bars" role="img" aria-label={`${b.alerts_now} alerts on the settings in force, ${b.alerts_then} on these`}>
        <span>In force</span><div><i className="now" style={{ width: `${(100 * b.alerts_now) / top}%` }} /></div><b>{b.alerts_now}</b>
        <span>These settings</span><div><i className="kept" style={{ width: `${(100 * b.kept) / top}%` }} />{b.added.count > 0 && <i className="added" style={{ width: `${(100 * b.added.count) / top}%` }} />}</div><b>{b.alerts_then}</b>
      </div>
      <div className="net-key"><i className="k now" />Alerts over the last {b.days} days<i className="k kept" />Still raised<i className="k added" />Newly raised</div>
      <div className="kpis slim">
        <div className="kpi"><span>Would not have been raised</span><b>{r.count}</b><small>{r.unproductive} closed with nothing found, {r.undecided} still open</small></div>
        <div className="kpi"><span>Of those, alerts that mattered</span><b className={r.productive ? 'bad' : ''}>{r.productive}</b><small>of {b.productive_now} that led to a filing or confirmed concern</small></div>
        <div className="kpi"><span>Newly raised</span><b>{b.added.count}</b><small>{b.added.count ? `on ${b.added.customers} customer${b.added.customers === 1 ? '' : 's'}` : 'nothing new'}</small></div>
      </div>
      {r.count > 0 && <details open={r.productive > 0}><summary>The {r.count} that would not have been raised</summary><Items rows={r.items} more={r.count - r.items.length} /></details>}
      {b.added.count > 0 && <details><summary>The {b.added.count} that would be new</summary><Items rows={b.added.items} more={b.added.count - b.added.items.length} /></details>}
      <p className="muted small">{b.caution} A backtest on this much history is a starting point, not proof.</p>
    </div>
  )
}
