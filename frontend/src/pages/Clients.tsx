import { useEffect, useState } from 'react'
import { api, fmtDateTime } from '../api'
import { switchClient } from '../App'
import { useApi } from '../useApi'
import { Card, Kpi, Loading, Pill } from '../components/ui'

type ClientRow = {
  id: string; name: string; template_title: string; region_label: string; deployment_label: string; status: string; created: string
  case_types: string[]; people: number; roles: number; queues: number; policy_version: string
  open_cases: number; cases: number; sla_breached: number; alerts: number; transactions: number
  own_workflow_versions: number; policy_versions: number; last_activity: string | null
}
type OverviewData = {
  tenants: ClientRow[]
  totals: { clients: number; live: number; open_cases: number; transactions: number; alerts: number; sla_breached: number }
  isolation: { model: string; enforced: string; next: string }
}
type Named = { id: string; title: string; domain: string }
type Template = {
  id: string; title: string; summary: string; case_types: Named[]
  access: { roles: { id: string; title: string; people: string[] }[]; queues: { id: string }[]; people: { id: string; name: string; title: string }[] }
}
type Templates = { templates: Template[]; regions: Record<string, string>; deployments: Record<string, string>; library: Named[] }
type Check = { ok: boolean; error?: string; case_types?: string[]; people?: number; roles?: number; queues?: number }
type Created = ClientRow & { sample_data: null | { cases: number; transactions: number; parties: number } }

const slug = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 24)
const n = (v: number) => v.toLocaleString('en-US')

function Onboard({ onDone }: { onDone: () => void }) {
  const { data, error } = useApi<Templates>('/platform/templates')
  const [name, setName] = useState('')
  const [id, setId] = useState('')
  const [touched, setTouched] = useState(false)
  const [template, setTemplate] = useState('full_service_bank')
  const [types, setTypes] = useState<string[] | null>(null)
  const [region, setRegion] = useState('us-east')
  const [deployment, setDeployment] = useState('pooled')
  const [sample, setSample] = useState(true)
  const [check, setCheck] = useState<Check | null>(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<Created | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const tpl = data?.templates.find(t => t.id === template)
  const chosen = types ?? tpl?.case_types.map(c => c.id) ?? []
  const body = { id, name, template, region, deployment, case_types: chosen, sample_data: sample }
  useEffect(() => {
    if (!id || !name) { setCheck(null); return }
    const timer = setTimeout(() => api.post<Check>('/platform/tenants/check', body).then(setCheck).catch(e => setCheck({ ok: false, error: e.message })), 250)
    return () => clearTimeout(timer)
  }, [id, name, template, chosen.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return <Card title="Onboard a client"><Loading error={error} /></Card>
  const toggle = (t: string) => setTypes(chosen.includes(t) ? chosen.filter(x => x !== t) : [...chosen, t])
  const submit = async () => {
    setBusy(true); setFailed(null)
    try {
      setDone(await api.post<Created>('/platform/tenants', body))
      setName(''); setId(''); setTouched(false); setTypes(null); setCheck(null); onDone()
    } catch (e) { setFailed((e as Error).message) } finally { setBusy(false) }
  }
  return (
    <Card title="Onboard a client" action={<span className="muted small">Pick a template, name the client, go. The client gets its own copy of everything.</span>}>
      <div className="tpls" role="radiogroup" aria-label="Template">
        {data.templates.map(t => (
          <button key={t.id} role="radio" aria-checked={t.id === template} className="tpl" onClick={() => { setTemplate(t.id); setTypes(null) }}>
            <b>{t.title}</b><span>{t.summary}</span>
            <small>{t.case_types.length} case types · {t.access.people.length} people in {t.access.roles.length} roles · {t.access.queues.length} queues</small>
          </button>))}
      </div>
      <div className="editor">
        <div>
          <div className="form">
            <label><span className="lbl">Client name</span>
              <input type="search" value={name} placeholder="Northwind Credit Union" onChange={e => { setName(e.target.value); if (!touched) setId(slug(e.target.value)) }} /></label>
            <label><span className="lbl">Client id (used in addresses and API calls)</span>
              <input type="search" value={id} placeholder="northwind" onChange={e => { setId(e.target.value); setTouched(true) }} /></label>
            <label><span className="lbl">Region</span>
              <select value={region} onChange={e => setRegion(e.target.value)}>{Object.entries(data.regions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
            <label><span className="lbl">Deployment</span>
              <select value={deployment} onChange={e => setDeployment(e.target.value)}>{Object.entries(data.deployments).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          </div>
          <span className="lbl">Case types to switch on</span>
          <div className="checks">{data.library.map(c => (
            <label key={c.id} className="check"><input type="checkbox" checked={chosen.includes(c.id)} onChange={() => toggle(c.id)} />{c.title} <span className="muted">· {c.domain}</span></label>))}</div>
          <label className="check"><input type="checkbox" checked={sample} onChange={e => setSample(e.target.checked)} />Load a small set of synthetic data so the client can be shown straight away</label>
          <div className="toolbar">
            <button className="primary" disabled={!check?.ok || busy} onClick={submit}>{busy ? 'Onboarding…' : 'Onboard client'}</button>
            {deployment !== 'pooled' && <span className="muted small">This build records the choice; every client still runs on the shared platform.</span>}
          </div>
          {failed && <p className="msg" role="alert">{failed}</p>}
        </div>
        <div>
          {done && <div className="verdict good" role="status"><b>{done.name} is live.</b>
            <p>{done.people} people in {done.roles} roles, {done.queues} queues and {done.case_types.length} case types, as its own configuration
              {done.sample_data ? `, with ${n(done.sample_data.parties)} customers, ${n(done.sample_data.transactions)} transactions and ${done.sample_data.cases} cases of synthetic data` : ''}.</p>
            <button onClick={() => switchClient(done.id)}>Open {done.name}</button></div>}
          {!check && !done && <p className="empty">Name the client to check the request. Onboarding validates everything first: every case type must be workable by the people and queues in the client's access policy.</p>}
          {check && !check.ok && <div className="verdict bad"><b>Cannot onboard yet</b><p>{check.error}</p></div>}
          {check?.ok && <div className="verdict good"><b>Ready to onboard</b>
            <p>{check.case_types!.length} case types, {check.people} people in {check.roles} roles and {check.queues} queues. Every workflow step has someone who can take it, and every four-eyes step has a second person.</p></div>}
          {tpl && <details open><summary><b>Who the {tpl.title.toLowerCase()} template starts with</b></summary>
            <table><tbody>{tpl.access.roles.filter(r => r.people.length).map(r => <tr key={r.id}><td><b>{r.title}</b></td><td className="small">{r.people.join(', ')}</td></tr>)}</tbody></table>
            <p className="muted small">Demo names. In production the people come from the client's identity provider; roles and queues are then changed by the client from its Access page.</p>
          </details>}
        </div>
      </div>
    </Card>
  )
}

export default function Clients() {
  const { data, error, reload } = useApi<OverviewData>('/platform/overview')
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const setStatus = (c: ClientRow, status: string) =>
    api.post(`/platform/tenants/${c.id}/status`, { status }).then(() => { setMsg(`${c.name} is now ${status}.`); reload() }).catch(e => setMsg(e.message))
  const t = data.totals
  return (
    <>
      <div className="page-h"><h1>Clients</h1><p>Every client on the platform. Each has its own people, access policy, workflows and data; nothing is shared between them except the platform itself.</p></div>
      <div className="kpis">
        <Kpi label="Clients" value={t.clients} sub={`${t.live} live`} />
        <Kpi label="Open cases" value={n(t.open_cases)} sub="across all clients" />
        <Kpi label="Past deadline" value={t.sla_breached} bad={t.sla_breached > 0} sub="open cases" />
        <Kpi label="Alerts" value={n(t.alerts)} />
        <Kpi label="Transactions held" value={n(t.transactions)} />
      </div>
      <Card title="Client book">
        <div className="scroll"><table>
          <thead><tr><th>Client</th><th>Runs on</th><th>Team</th><th>Workloads</th><th className="num">Open cases</th><th className="num">Past deadline</th>
            <th className="num">Alerts</th><th className="num">Transactions</th><th>Own configuration</th><th>Last activity</th><th /></tr></thead>
          <tbody>{data.tenants.map(c => (
            <tr key={c.id}>
              <td><b>{c.name}</b> {c.status === 'live' ? <Pill tone="good">live</Pill> : <Pill tone="critical">suspended</Pill>}<div className="muted small">{c.id} · {c.template_title} template</div></td>
              <td>{c.region_label}<div className="muted small">{c.deployment_label}</div></td>
              <td>{c.people} people<div className="muted small">{c.roles} roles · {c.queues} queues</div></td>
              <td>{c.case_types.length} case types</td>
              <td className="num">{n(c.open_cases)}<div className="muted small">of {n(c.cases)}</div></td>
              <td className="num">{c.sla_breached ? <Pill tone="critical">{c.sla_breached}</Pill> : 0}</td>
              <td className="num">{n(c.alerts)}</td><td className="num">{n(c.transactions)}</td>
              <td className="small">Access policy v{c.policy_version}{c.policy_versions > 1 && ` (${c.policy_versions} versions)`}
                <div className="muted">{c.own_workflow_versions ? `${c.own_workflow_versions} workflow version${c.own_workflow_versions > 1 ? 's' : ''} of its own` : 'Platform workflows'}</div></td>
              <td className="small nowrap">{c.last_activity ? fmtDateTime(c.last_activity) : '–'}</td>
              <td className="nowrap"><button className="ghost" disabled={c.status !== 'live'} onClick={() => switchClient(c.id)}>Open</button>
                <button className="ghost" onClick={() => setStatus(c, c.status === 'live' ? 'suspended' : 'live')}>{c.status === 'live' ? 'Suspend' : 'Resume'}</button></td>
            </tr>))}</tbody>
        </table></div>
        {msg && <p className="msg" role="status">{msg}</p>}
        <p className="muted small">This console shows counts only. It cannot list a customer, open a case or read a narrative: that takes a sign-in at the client, under the client's own access policy.</p>
      </Card>
      <Onboard onDone={reload} />
      <Card title="How clients are kept apart">
        <dl className="facts wide">
          <dt>Data</dt><dd>{data.isolation.model}. Customer, account and transaction ids are unique per client, so two clients can use the same ids.</dd>
          <dt>Enforcement</dt><dd>{data.isolation.enforced}. Statements that cannot be filtered are refused.</dd>
          <dt>Configuration</dt><dd>Each client has its own versioned access policy, its own choice of case types and its own workflow versions. A change at one client never reaches another.</dd>
          <dt>Proof</dt><dd>Automated tests give every table rows for two clients and check that neither can read, change or delete the other's, then run ten clients side by side and probe every address across clients.</dd>
          <dt>Next</dt><dd>{data.isolation.next}.</dd>
        </dl>
      </Card>
    </>
  )
}
