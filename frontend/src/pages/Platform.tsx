import { useState } from 'react'
import { api, fmtDateTime } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Loading, Pill } from '../components/ui'

type Item = { name: string; status: string; note: string; kind?: string }
type Integrations = { connectors: Item[]; interfaces: Item[] }
type Stream = { stream: string; label: string; topic: string; received: number; loaded: number; duplicates: number; rejected: number; alerts: number; last_at: string | null; last_reject: string }
type Backbone = {
  bus: { kind: string; note: string; workers: boolean }; publisher: string
  outbox: { waiting: number | null; oldest_waiting: string | null; published: number | null; last_published: string | null; total: number; by_type: { type: string; count: number }[] }
  topics: { out: string[]; in: string[]; dead_letter: string }; streams: Stream[]
}
type Bench = { events: number; parties: number; map_seconds: number; detect_seconds: number; map_per_sec: number; end_to_end_per_sec: number; alerts: number }

const Status = ({ s }: { s: string }) => <Pill tone={s === 'live' ? 'good' : 'neutral'}>{s === 'live' ? 'Live in demo' : 'Planned'}</Pill>
const TARGET = [
  ['Event backbone', 'Outbox in the database; Kafka when one is connected', 'Apache Kafka, with Debezium on the outbox'], ['Stream detection', 'Python consumers in micro-batches', 'Apache Flink'],
  ['Workflow', 'Synchronous service calls', 'Temporal durable workflows'], ['Case store', 'SQLite or Postgres', 'Postgres, partitioned per tenant'],
  ['History and analytics', 'Operational tables', 'Apache Iceberg lakehouse'], ['Search', 'SQL filters', 'OpenSearch'],
  ['Agents', 'Offline rule-and-template provider', 'LLM gateway with hosted or self-hosted models'], ['Identity', 'Persona switch', 'OIDC via Keycloak'],
]

const BUS: Record<string, [string, string]> = { none: ['neutral', 'No bus connected'], memory: ['warning', 'In this process only'], kafka: ['good', 'Kafka'] }

/** This client's side of the event backbone: what has gone out, what is waiting, and what its inbound streams have taken in. */
function BackboneCard() {
  const { data } = useApi<Backbone>('/backbone')
  if (!data) return null
  const o = data.outbox
  const [tone, label] = BUS[data.bus.kind] ?? ['neutral', data.bus.kind]
  return (
    <Card title="Event backbone" action={<Pill tone={tone}>{label}</Pill>}>
      <p>Every audit event and every new alert is written to an outbox in the same transaction as the change, then published as a CloudEvent to this client's own topics. {data.bus.note}</p>
      <div className="kpis">
        <Kpi label="Events recorded" value={o.total.toLocaleString()} sub={data.publisher === 'debezium' ? 'published by change data capture' : 'published by the relay'} />
        {o.waiting !== null && <Kpi label="Waiting to go out" value={o.waiting.toLocaleString()} sub={o.oldest_waiting ? `oldest from ${fmtDateTime(o.oldest_waiting)}` : 'nothing waiting'} />}
        {o.published !== null && <Kpi label="Published" value={o.published.toLocaleString()} sub={o.last_published ? `last ${fmtDateTime(o.last_published)}` : 'none yet'} />}
      </div>
      <table><thead><tr><th>Inbound stream</th><th className="num">Received</th><th className="num">Loaded</th><th className="num">Already held</th><th className="num">Set aside</th><th className="num">Alerts</th><th>Last message</th></tr></thead>
        <tbody>{data.streams.map(x => <tr key={x.stream}>
          <td><b>{x.label}</b><div className="muted small">{x.topic}</div></td>
          <td className="num">{x.received.toLocaleString()}</td><td className="num">{x.loaded.toLocaleString()}</td><td className="num">{x.duplicates.toLocaleString()}</td>
          <td className="num" title={x.last_reject}>{x.rejected.toLocaleString()}</td><td className="num">{x.alerts.toLocaleString()}</td>
          <td>{x.last_at ? fmtDateTime(x.last_at) : <span className="muted">Nothing yet</span>}</td></tr>)}</tbody></table>
      <p className="muted small">Out: {data.topics.out.join(', ')}. Records that cannot be loaded go to {data.topics.dead_letter} with the reason.</p>
    </Card>
  )
}

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
      <BackboneCard />
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
        <p className="muted small">This is a single-process measurement on your laptop, not a production figure. Billion-event days need the distributed path in the table below.</p>
      </Card>
      <Card title="From laptop build to cloud build">
        <table><thead><tr><th>Capability</th><th>This build</th><th>Target architecture</th></tr></thead>
          <tbody>{TARGET.map(r => <tr key={r[0]}><td><b>{r[0]}</b></td><td>{r[1]}</td><td>{r[2]}</td></tr>)}</tbody></table>
      </Card>
    </>
  )
}
