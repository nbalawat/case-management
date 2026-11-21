import { Link } from 'react-router-dom'
import { Pill } from '../ui'
import type { Check } from './types'

/** What a check said: validity, the analysis, what publishing changes, and the replay on recent cases. */
export default function CheckPanel({ check }: { check: Check }) {
  if (!check.ok) return <div className="verdict bad" role="alert"><b>Not valid</b><p>{check.error}</p></div>
  const a = check.analysis!
  const sim = check.simulation
  return (
    <>
      {a.errors.length > 0
        ? <div className="verdict bad"><b>Cannot be published</b><ul>{a.errors.map(e => <li key={e}>{e}</li>)}</ul></div>
        : <div className="verdict good"><b>Valid. Publishing would:</b><ul>{check.changes!.map(c => <li key={c}>{c}</li>)}</ul></div>}
      {a.warnings.length > 0 && <div className="verdict warn"><b>Worth a look</b><ul>{a.warnings.map(w => <li key={w}>{w}</li>)}</ul></div>}
      <p className="muted small">
        {a.checked ? `Checked every one of ${a.combinations.toLocaleString()} distinct input combinations for overlaps, dead rules and gaps.` : a.note}
      </p>
      {sim && (
        <div className="dt-sim">
          <h3>Replayed on the {sim.cases} most recent case{sim.cases === 1 ? '' : 's'}</h3>
          {sim.compared_with && (
            <p><b>{sim.changed}</b> would get a different {sim.compared_with} from the one they have{sim.errors ? `; ${sim.errors} broke the hit policy` : ''}.</p>
          )}
          <div className="dt-bars">
            {Object.entries(sim.by_rule).sort((x, y) => y[1] - x[1]).map(([rule, n]) => (
              <div key={rule} className="dt-bar"><span>{rule}</span><i style={{ width: `${Math.max(2, (100 * n) / Math.max(sim.cases, 1))}%` }} /><b>{n}</b></div>
            ))}
          </div>
          {sim.examples.length > 0 && (
            <table className="small"><thead><tr><th>Case</th><th>Has</th><th>Would get</th><th>Rule</th></tr></thead>
              <tbody>{sim.examples.slice(0, 8).map(e => (
                <tr key={e.case_id}><td><Link to={`/cases/${e.case_id}`}>{e.case_id}</Link></td><td>{e.actual ?? '–'}</td><td><b>{String(e.would_be)}</b></td>
                  <td>{e.rules.map(r => <Pill key={r}>{r}</Pill>)}</td></tr>))}</tbody></table>
          )}
          <p className="muted small">Already-open cases are not changed by publishing; the table applies to cases that open afterwards.</p>
        </div>
      )}
    </>
  )
}
