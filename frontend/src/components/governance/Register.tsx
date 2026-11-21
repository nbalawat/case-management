import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDateTime } from '../../api'
import { useNameOf } from '../../App'
import { Card, Pill } from '../ui'
import type { Entry } from './types'

/** Every change to how the platform is set up, from wherever it was made, newest first. */
export default function Register({ rows, areas }: { rows: Entry[]; areas: string[] }) {
  const nameOf = useNameOf()
  const [area, setArea] = useState('')
  const [all, setAll] = useState(false)
  const shown = rows.filter(e => !area || e.area === area)
  const present = areas.filter(a => rows.some(e => e.area === a))
  return (
    <Card title={`Change register (${shown.length})`} action={<button onClick={() => api.download(`/governance/register.csv${area ? `?area=${encodeURIComponent(area)}` : ''}`)}>Download</button>}>
      <div className="seg small"><button aria-pressed={!area} onClick={() => setArea('')}>Everything</button>{present.map(a => <button key={a} aria-pressed={area === a} onClick={() => setArea(a)}>{a}</button>)}</div>
      <div className="scroll"><table><thead><tr><th>When</th><th>Area</th><th>What changed</th><th>By</th><th>Second person</th></tr></thead>
        <tbody>{(all ? shown : shown.slice(0, 12)).map((e, i) => <tr key={i}>
          <td className="nowrap">{fmtDateTime(e.at)}</td><td><Link to={e.link}>{e.area}</Link></td>
          <td><b>{e.subject}</b>: {e.what}{e.note && <div className="muted small">“{e.note}”</div>}</td>
          <td>{nameOf(e.by)}</td><td>{e.second ? <Pill tone="good">{nameOf(e.second)}</Pill> : <span className="muted">–</span>}</td></tr>)}</tbody></table></div>
      {shown.length > 12 && <button className="ghost" onClick={() => setAll(!all)}>{all ? 'Show the latest only' : `Show all ${shown.length}`}</button>}
      <p className="muted small">Read from each area's own version history: nothing is copied here, so nothing can drift from what is in force.</p>
    </Card>
  )
}
