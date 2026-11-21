import { RefreshCw } from 'lucide-react'
import { useApi } from '../../useApi'
import { Card, Loading, Pill } from '../ui'

type Check = { name: string; label: string; required: boolean; ok: boolean; note: string; ms: number }
type Health = {
  status: 'ready' | 'degraded' | 'unready'; ready: boolean; checks: Check[]
  uptime_seconds: number; version: string; python: string; schema_head: string; tracing: boolean; log_format: string
}

const TONE = { ready: 'good', degraded: 'warning', unready: 'critical' } as const
const LABEL = { ready: 'All checks pass', degraded: 'Running, with an optional service down', unready: 'Not ready' }

function uptime(s: number) {
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60)
  return d ? `${d} d ${h} h` : h ? `${h} h ${m} min` : `${m} min`
}

/** The platform's own health, as the readiness probe and the operator see it. */
export default function HealthCard() {
  const { data, error, reload } = useApi<Health>('/platform/health')
  return (
    <Card title="Platform health" action={<span className="toolbar">
      {data && <Pill tone={TONE[data.status]}>{LABEL[data.status]}</Pill>}
      <button className="ghost" onClick={reload} aria-label="Check again"><RefreshCw size={14} /></button></span>}>
      {!data ? <Loading error={error} /> : <>
        <table>
          <thead><tr><th>Check</th><th>Needed for readiness</th><th>Result</th><th className="num">Time</th></tr></thead>
          <tbody>{data.checks.map(c => (
            <tr key={c.name}>
              <td><b>{c.label}</b></td>
              <td>{c.required ? 'Yes' : <span className="muted">No, the platform falls back without it</span>}</td>
              <td>{c.ok ? <Pill tone="good">ok</Pill> : <Pill tone={c.required ? 'critical' : 'warning'}>down</Pill>} <span className="small muted">{c.note}</span></td>
              <td className="num small">{c.ms} ms</td>
            </tr>))}</tbody>
        </table>
        <p className="muted small">
          Version {data.version}, schema {data.schema_head}, Python {data.python}, up {uptime(data.uptime_seconds)}.
          Logs are {data.log_format === 'json' ? 'JSON lines' : 'plain text'}; traces are {data.tracing ? 'sent over OTLP' : 'off (set OTEL_EXPORTER_OTLP_ENDPOINT)'}.
          Probes: <code>/healthz</code> and <code>/readyz</code>. Prometheus metrics: <code>/metrics</code>, or the metrics port in a deployment.
          On a laptop, <code>make observability</code> adds Prometheus, Grafana and Jaeger.
        </p>
      </>}
    </Card>
  )
}
