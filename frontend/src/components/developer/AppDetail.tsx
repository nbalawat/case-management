import { useState } from 'react'
import { api, fmtDateTime } from '../../api'
import { useApi } from '../../useApi'
import { useNameOf } from '../../shell/people'
import { Card, Loading, Pill } from '../ui'
import type { AppDetail as Detail } from './types'

const TONE: Record<string, string> = { active: 'good', suspended: 'warning', revoked: 'critical', disabled: 'critical', paused: 'warning', delivered: 'good', failed: 'critical', pending: 'warning' }

export function Secret({ label, value, onDone }: { label: string; value: string; onDone: () => void }) {
  return (
    <div className="dev-secret" role="status">
      <b>{label}</b>
      <code>{value}</code>
      <div className="inline"><button onClick={() => navigator.clipboard?.writeText(value)}>Copy</button>
        <button className="ghost" onClick={onDone}>I have stored it</button>
        <span className="muted small">It is not shown again. Only a keyed hash is kept.</span></div>
    </div>
  )
}

export default function AppDetail({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { data, error, reload } = useApi<Detail>(`/developer/clients/${id}`, [id])
  const nameOf = useNameOf()
  const [secret, setSecret] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const done = () => { reload(); onChanged() }
  const rotate = () => api.post<{ client_secret: string }>(`/developer/clients/${id}/secret`, { overlap_hours: 24 })
    .then(r => { setSecret(r.client_secret); done() }).catch(e => setMsg(e.message))
  const status = (s: string) => api.post(`/developer/clients/${id}/status`, { status: s, reason: `Set to ${s} on the developer page` }).then(done).catch(e => setMsg(e.message))
  return (
    <>
      <Card title={data.name} action={<Pill tone={TONE[data.status]}>{data.status}</Pill>}>
        {secret && <Secret label="New client secret. The old one keeps working for 24 hours." value={secret} onDone={() => setSecret(null)} />}
        <dl className="dev-kv">
          <dt>Client id</dt><dd><code>{data.client_id}</code></dd>
          <dt>Acts as</dt><dd>{data.roles.join(', ')}{data.queues.length ? ` in ${data.queues.join(', ')}` : ''}</dd>
          <dt>Scopes</dt><dd className="dev-choices">{data.scopes.map(s => <code key={s}>{s}</code>)}</dd>
          <dt>Addresses</dt><dd>{data.allowed_ips.length ? data.allowed_ips.join(', ') : 'Any'}</dd>
          <dt>Limits</dt><dd>{data.rate_limit} requests a minute; tokens live {Math.round(data.token_ttl / 60)} minutes</dd>
          <dt>Registered</dt><dd>{fmtDateTime(data.created)} by {nameOf(data.created_by)}</dd>
          <dt>Last used</dt><dd>{data.last_used ? fmtDateTime(data.last_used) : 'Never'}</dd>
          <dt>Secrets</dt><dd>{data.secrets.map(s => <div key={s.id} className="small">…{s.hint} · {s.live ? (s.expires ? `ends ${fmtDateTime(s.expires)}` : 'live') : 'ended'}</div>)}</dd>
        </dl>
        {data.status !== 'revoked' && <div className="inline" style={{ marginTop: 12 }}>
          {data.status === 'active' && <button onClick={rotate}>Rotate secret</button>}
          {data.status === 'active' ? <button onClick={() => status('suspended')}>Suspend</button> : <button onClick={() => status('active')}>Resume</button>}
          <button className="ghost" onClick={() => status('revoked')}>Revoke for good</button>
          <span className="muted small">Suspending or revoking ends every token at once.</span></div>}
        {msg && <p className="msg" role="status">{msg}</p>}
      </Card>
      <Card title="Recent calls" action={<span className="muted small">{data.usage.calls} in the last 200 · {data.usage.errors} errors · p95 {data.usage.p95_ms ?? '–'} ms</span>}>
        {data.calls.length === 0 ? <p className="empty">No calls yet.</p> : <div className="scroll"><table>
          <thead><tr><th>When</th><th>Call</th><th>Answer</th><th>Time</th><th>Request id</th></tr></thead>
          <tbody>{data.calls.map(c => <tr key={c.request_id + c.ts}><td className="small">{fmtDateTime(c.ts)}</td><td><code>{c.method} {c.route}</code></td>
            <td><Pill tone={c.status < 400 ? 'good' : c.status === 429 ? 'warning' : 'critical'}>{c.status}</Pill></td><td className="small">{c.ms} ms</td>
            <td className="small muted">{c.request_id.slice(0, 12)}</td></tr>)}</tbody></table></div>}
      </Card>
      <Card title="Webhooks">
        {data.webhooks.length === 0 ? <p className="empty">This application has registered no webhook endpoints. It does that itself through the API.</p> :
          data.webhooks.map(w => (
            <div key={w.id} style={{ marginBottom: 12 }}>
              <div className="inline"><code>{w.url}</code><Pill tone={TONE[w.status]}>{w.status}</Pill>
                <span className="muted small">{w.event_types.length ? w.event_types.join(', ') : 'every event it may see'}</span></div>
              {w.disabled_reason && <p className="small">{w.disabled_reason}</p>}
              <div className="small muted">{w.recent.map(d => `${d.event_type} ${d.status}${d.last_code ? ` (${d.last_code})` : ''}`).join(' · ') || 'No deliveries yet'}</div>
            </div>))}
      </Card>
      <Card title="History">
        <ul className="dev-rules">{data.history.map((h, i) => <li key={i}>{fmtDateTime(h.at)}: {h.what} by {nameOf(h.by)}</li>)}</ul>
      </Card>
    </>
  )
}
