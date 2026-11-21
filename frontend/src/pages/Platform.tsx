import { useState } from 'react'
import { api } from '../api'
import { useApi } from '../useApi'
import { Card, Loading, Pill } from '../components/ui'

type Item = { name: string; status: string; note: string; kind?: string }
type Integrations = { connectors: Item[]; interfaces: Item[] }
type Bench = { events: number; parties: number; map_seconds: number; detect_seconds: number; map_per_sec: number; end_to_end_per_sec: number; alerts: number }

const Status = ({ s }: { s: string }) => <Pill tone={s === 'live' ? 'good' : 'neutral'}>{s === 'live' ? 'Live in demo' : 'Planned'}</Pill>
const TARGET = [
  ['Event backbone', 'In-process', 'Apache Kafka'], ['Stream detection', 'Python batch per feed', 'Apache Flink'],
  ['Workflow', 'Synchronous service calls', 'Temporal durable workflows'], ['Case store', 'SQLite or Postgres', 'Postgres, partitioned per tenant'],
  ['History and analytics', 'Operational tables', 'Apache Iceberg lakehouse'], ['Search', 'SQL filters', 'OpenSearch'],
  ['Agents', 'Offline rule-and-template provider', 'LLM gateway with hosted or self-hosted models'], ['Identity', 'Persona switch', 'OIDC via Keycloak'],
]

export default function Platform() {
  const { data, error } = useApi<Integrations>('/integrations')
  const [n, setN] = useState(100000)
  const [bench, setBench] = useState<Bench | null>(null)
  const [busy, setBusy] = useState(false)
  const run = async () => { setBusy(true); try { setBench(await api.post<Bench>('/scale/benchmark', { events: n })) } finally { setBusy(false) } }
  return (
    <>
      <div className="page-h"><h1>Integrations and scale</h1><p>What runs in this laptop build today, and what each part becomes in the cloud build.</p></div>
      {!data ? <Loading error={error} /> : <div className="cols-2">
        <Card title="Source connectors"><table><tbody>{data.connectors.map(c => <tr key={c.name}><td><b>{c.name}</b><div className="muted small">{c.note}</div></td><td>{c.kind}</td><td><Status s={c.status} /></td></tr>)}</tbody></table></Card>
        <Card title="Interfaces"><table><tbody>{data.interfaces.map(c => <tr key={c.name}><td><b>{c.name}</b><div className="muted small">{c.note}</div></td><td><Status s={c.status} /></td></tr>)}</tbody></table>
          <p><a href="/docs" target="_blank" rel="noreferrer">Open the interactive API reference</a></p></Card>
      </div>}
      <Card title="Throughput check on this machine">
        <p>Pushes synthetic events through the same mapping and detection code the onboarding page uses, in one Python process, and reports the measured rate.</p>
        <div className="toolbar">
          <select value={n} onChange={e => setN(+e.target.value)} aria-label="Event count">{[50000, 100000, 250000, 500000].map(v => <option key={v} value={v}>{v.toLocaleString()} events</option>)}</select>
          <button className="primary" onClick={run} disabled={busy}>{busy ? 'Running…' : 'Run benchmark'}</button>
        </div>
        {bench && <div className="kpis">
          <div className="kpi"><span>Mapping rate</span><b>{bench.map_per_sec.toLocaleString()}/s</b><small>{bench.map_seconds}s for {bench.events.toLocaleString()} events</small></div>
          <div className="kpi"><span>Mapping plus detection</span><b>{bench.end_to_end_per_sec.toLocaleString()}/s</b><small>{bench.detect_seconds}s detection across {bench.parties.toLocaleString()} customers</small></div>
          <div className="kpi"><span>Alerts raised</span><b>{bench.alerts}</b><small>three scenarios; pass-through excluded here</small></div>
        </div>}
        <p className="muted small">This is a single-process measurement on your laptop, not a production figure. Billion-event days need the streaming path in the table below, which milestone 2 adds.</p>
      </Card>
      <Card title="From laptop build to cloud build">
        <table><thead><tr><th>Capability</th><th>This build</th><th>Target architecture</th></tr></thead>
          <tbody>{TARGET.map(r => <tr key={r[0]}><td><b>{r[0]}</b></td><td>{r[1]}</td><td>{r[2]}</td></tr>)}</tbody></table>
      </Card>
    </>
  )
}
