import { useState } from 'react'
import { useApi } from '../useApi'
import { Card, Loading, Pill } from '../components/ui'

type CT = {
  case_type: string; title: string; version: string; initial_state: string; yaml: string
  states: Record<string, { label: string; kind: string }>
  transitions: { from: string; to: string; label: string; roles: string[]; requires?: string[]; four_eyes?: boolean }[]
  sla?: { clock: string; due_days: number }; agents?: { on_create: string[]; autonomy: string }
}
type Scenario = { name: string; version: string; title: string; typology: string; params: Record<string, number> }

export default function Workflows() {
  const types = useApi<CT[]>('/case-types')
  const scen = useApi<Scenario[]>('/scenarios')
  const [sel, setSel] = useState(0)
  if (!types.data) return <Loading error={types.error} />
  const ct = types.data[sel]
  return (
    <>
      <div className="page-h"><h1>Workflows</h1><p>Case types are versioned configuration. Changing a workflow means editing a file and promoting it, not writing code.</p></div>
      <div className="seg" role="group" aria-label="Case type">
        {types.data.map((t, i) => <button key={t.case_type} aria-pressed={i === sel} onClick={() => setSel(i)}>{t.title}</button>)}
      </div>
      <div className="cols-2">
        <Card title={`${ct.title} · v${ct.version}`}>
          <div className="states">{Object.entries(ct.states).map(([id, s]) => (
            <div key={id} className={`state ${s.kind}`}>
              <b>{s.label}</b>{id === ct.initial_state && <Pill>start</Pill>}{s.kind === 'closed' && <Pill tone="neutral">end</Pill>}
              <ul>{ct.transitions.filter(t => t.from === id).map(t => (
                <li key={t.to}>{t.label} <span className="muted">to {ct.states[t.to].label}</span>
                  <div>{t.roles.map(r => <Pill key={r} tone="human">{r}</Pill>)}{t.four_eyes && <Pill tone="serious">four-eyes</Pill>}{t.requires?.map(r => <Pill key={r} tone="warning">needs {r}</Pill>)}</div></li>))}</ul>
            </div>))}</div>
          <dl className="facts">
            <dt>Deadline clock</dt><dd>{ct.sla ? `${ct.sla.clock}, ${ct.sla.due_days} days` : '–'}</dd>
            <dt>Agents on creation</dt><dd>{ct.agents?.on_create.map(a => <Pill key={a} tone="agent">{a.replaceAll('_', ' ')}</Pill>)}</dd>
            <dt>Agent autonomy</dt><dd>{ct.agents?.autonomy} · agents recommend, people decide</dd>
          </dl>
        </Card>
        <Card title="Configuration source"><pre>{ct.yaml}</pre></Card>
      </div>
      <Card title="Detection scenarios">
        {!scen.data ? <Loading error={scen.error} /> : <table>
          <thead><tr><th>Scenario</th><th>Typology</th><th>Version</th><th>Parameters</th></tr></thead>
          <tbody>{scen.data.map(s => <tr key={s.name}><td><b>{s.title}</b><div className="muted small">{s.name}</div></td><td>{s.typology}</td><td>{s.version}</td>
            <td className="small">{Object.entries(s.params).map(([k, v]) => <Pill key={k}>{k}: {v}</Pill>)}</td></tr>)}</tbody></table>}
      </Card>
    </>
  )
}
