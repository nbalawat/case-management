import { useState } from 'react'
import { fmtDateTime } from '../api'
import { useApi } from '../useApi'
import PageTabs from '../components/layout/PageTabs'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import AppDetail, { Secret } from '../components/developer/AppDetail'
import QuickStart from '../components/developer/QuickStart'
import Register from '../components/developer/Register'
import type { Overview } from '../components/developer/types'
import '../styles/developer.css'

export default function Developer() {
  const { data, error, reload } = useApi<Overview>('/developer')
  const [open, setOpen] = useState<string | null>(null)
  const [made, setMade] = useState<{ id: string; secret: string } | null>(null)
  if (!data) return <Loading error={error} />
  const live = data.clients.filter(c => c.status === 'active')
  const calls = data.clients.reduce((n, c) => n + (c.usage?.calls ?? 0), 0)
  const errors = data.clients.reduce((n, c) => n + (c.usage?.errors ?? 0), 0)
  const selected = open ?? data.clients[0]?.client_id ?? null
  const apps = (
    <div className="dev-grid">
      <div className="ws-stack">
        {made && <Secret label={`Registered. Client id ${made.id}. Its secret:`} value={made.secret} onDone={() => setMade(null)} />}
        <Card title="Applications">
          {data.clients.length === 0 ? <p className="empty">No applications yet. Register one below.</p> :
            <div className="dev-apps">{data.clients.map(c => (
              <button key={c.client_id} className="dev-app" aria-current={c.client_id === selected} onClick={() => setOpen(c.client_id)}>
                <div className="row"><b>{c.name}</b><Pill tone={c.status === 'active' ? 'good' : c.status === 'suspended' ? 'warning' : 'critical'}>{c.status}</Pill></div>
                <div className="small muted">{c.roles.join(', ')} · {c.scopes.length} scopes · {c.usage?.calls ?? 0} calls today
                  {c.last_used ? ` · last ${fmtDateTime(c.last_used)}` : ''}</div>
              </button>))}</div>}
        </Card>
        <Register data={data} onMade={a => { setMade({ id: a.client_id, secret: a.client_secret }); setOpen(a.client_id); reload() }} />
      </div>
      <div className="ws-stack">{selected ? <AppDetail id={selected} onChanged={reload} /> : <Card><p className="empty">Choose an application.</p></Card>}</div>
    </div>
  )
  return (
    <>
      <div className="page-h"><h1>Developer</h1><p>Applications that call the public API for this client: what each may do, its credentials, and what it has been doing.</p></div>
      <div className="kpis">
        <Kpi label="Applications" value={live.length} sub={`${data.clients.length - live.length} suspended or revoked`} />
        <Kpi label="Calls, last 24 hours" value={calls.toLocaleString()} sub={`${data.people_usage.calls} more from people's own tokens`} />
        <Kpi label="Errors" value={errors} bad={errors > 0} sub="4xx and 5xx answers" />
        <Kpi label="API version" value={data.api.version} sub={data.api.base_url} />
      </div>
      <PageTabs label="Developer" tabs={[
        { id: 'apps', label: 'Applications', count: live.length, content: apps },
        { id: 'start', label: 'Quick start and reference', content: <QuickStart data={data} /> },
      ]} />
    </>
  )
}
