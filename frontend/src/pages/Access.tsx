import { useState } from 'react'
import { words } from '../api'
import { useApi } from '../useApi'
import { Card, Loading, Pill } from '../components/ui'

type AccessData = {
  policy: { name: string; version: string }
  permissions: { id: string; label: string }[]
  roles: { id: string; title: string; permissions: string[]; read_only: boolean; people: string[] }[]
  queues: { id: string; label: string; members: string[] }[]
  steps: { case_type: string; title: string; from: string; label: string; roles: string[]; four_eyes: boolean; when: string }[]
}
const Yes = () => <span className="yes" aria-label="allowed">●</span>
const No = () => <span className="no" aria-label="not allowed">·</span>

export default function Access() {
  const { data, error } = useApi<AccessData>('/access')
  const [type, setType] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const types = [...new Map(data.steps.map(s => [s.case_type, s.title])).entries()]
  const current = type ?? types[0][0]
  const steps = data.steps.filter(s => s.case_type === current)
  const used = data.roles.filter(r => steps.some(s => s.roles.includes(r.id)))
  return (
    <>
      <div className="page-h"><h1>Access</h1>
        <p>Who can do what. Everything on this page is read from one policy file (<code>{data.policy.name}</code>, v{data.policy.version}) and from the case types. No role is written into code.</p></div>
      <Card title="Roles and permissions">
        <div className="scroll"><table className="matrix">
          <thead><tr><th>Role</th><th>Held by</th>{data.permissions.map(p => <th key={p.id} title={p.label}>{words(p.id)}</th>)}</tr></thead>
          <tbody>{data.roles.map(r => (
            <tr key={r.id}><td><b>{r.title}</b>{r.read_only && <Pill tone="warning">read-only</Pill>}</td><td className="small">{r.people.join(', ') || '–'}</td>
              {data.permissions.map(p => <td key={p.id} className="dot" title={`${r.title}: ${p.label}`}>{r.permissions.includes(p.id) ? <Yes /> : <No />}</td>)}</tr>))}</tbody>
        </table></div>
        <ul className="legend-list">{data.permissions.map(p => <li key={p.id}><b>{words(p.id)}</b> {p.label.split(' - ')[1] ?? p.label}</li>)}</ul>
      </Card>
      <Card title="Workflow steps by role">
        <div className="seg" role="group" aria-label="Case type">
          {types.map(([id, title]) => <button key={id} aria-pressed={id === current} onClick={() => setType(id)}>{title}</button>)}
        </div>
        <div className="scroll"><table className="matrix">
          <thead><tr><th>From stage</th><th>Step</th>{used.map(r => <th key={r.id}>{r.title}</th>)}<th>Controls</th></tr></thead>
          <tbody>{steps.map((s, i) => (
            <tr key={i}><td>{s.from}</td><td><b>{s.label}</b></td>
              {used.map(r => <td key={r.id} className="dot">{s.roles.includes(r.id) ? <Yes /> : <No />}</td>)}
              <td className="small">{s.four_eyes && <Pill tone="serious">four-eyes</Pill>}{s.when && <Pill tone="warning">only if {s.when}</Pill>}</td></tr>))}</tbody>
        </table></div>
        <p className="muted small">An automated test walks every person through every step in this table and checks that the platform allows exactly what is shown.</p>
      </Card>
      <Card title="Queues and their members">
        <table><thead><tr><th>Queue</th><th>Members</th></tr></thead>
          <tbody>{data.queues.map(q => <tr key={q.id}><td><b>{q.label}</b><div className="muted small">{q.id}</div></td><td>{q.members.join(', ') || <span className="muted">Nobody</span>}</td></tr>)}</tbody></table>
      </Card>
    </>
  )
}
