import { useState } from 'react'
import { api, fmtDateTime, words } from '../api'
import { usePeople } from '../shell/people'
import { useApi } from '../useApi'
import { Card, Loading, Pill } from '../components/ui'

type Policy = {
  policy: { name: string; version: string }
  permissions: { id: string; label: string }[]
  roles: { id: string; title: string; permissions: string[]; read_only: boolean; people: string[]; sees: string; limited: boolean; restricted: boolean }[]
  queues: { id: string; label: string; members: string[] }[]
  people: { id: string; name: string; title: string; roles: string[]; queues: string[]; read_only: boolean; sees: string[]; restricted: boolean }[]
}
type AccessData = Policy & {
  tenant: { id: string; name: string }; yaml: string; can_edit: boolean
  history: { version: string; published_by: string; published: string; note: string; active: boolean }[]
  steps: { case_type: string; title: string; from: string; label: string; roles: string[]; four_eyes: boolean; when: string }[]
}
type Check = { ok: boolean; error?: string; changes?: string[]; warnings?: string[]; preview?: Policy }
const Yes = () => <span className="yes" aria-label="allowed">●</span>
const No = () => <span className="no" aria-label="not allowed">·</span>

function Editor({ data, onPublished }: { data: AccessData; onPublished: () => void }) {
  const { holder, nameOf } = usePeople()
  const [src, setSrc] = useState(data.yaml)
  const [note, setNote] = useState('')
  const [check, setCheck] = useState<Check | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [version, setVersion] = useState(data.policy.version)
  if (version !== data.policy.version) { setVersion(data.policy.version); setSrc(data.yaml); setCheck(null) }  // a new version was published: edit from it
  const run = () => api.post<Check>('/access/check', { source: src }).then(r => { setCheck(r); setMsg(null) }).catch(e => setMsg(e.message))
  const publish = () => api.post<{ version: string }>('/access/publish', { source: src, note })
    .then(r => { setMsg(`Published v${r.version}. It applies from the next action anyone takes.`); setNote(''); onPublished() }).catch(e => setMsg(e.message))
  return (
    <Card title="Change the policy" action={<span className="muted small">Raise the version number, check, then publish</span>}>
      <div className="editor">
        <div>
          <textarea className="code" rows={22} spellCheck={false} value={src} onChange={e => { setSrc(e.target.value); setCheck(null) }} aria-label="Access policy" />
          <div className="toolbar">
            <button onClick={run}>Check draft</button>
            <input type="search" placeholder="What changed and why" value={note} onChange={e => setNote(e.target.value)} aria-label="Change note" />
            <button className="primary" disabled={!data.can_edit || !check?.ok} onClick={publish}
              title={!data.can_edit ? 'Publishing needs the manage access permission' : !check?.ok ? 'Check the draft first' : ''}>Publish new version</button>
            {!data.can_edit && <span className="muted small">Sign in as {holder('manage_access')} to publish.</span>}
          </div>
          {msg && <p className="msg" role="status">{msg}</p>}
        </div>
        <div>
          {!check && <p className="empty">Check the draft to see its effect. A policy is refused if it would leave any workflow {data.tenant.name} runs without someone able to take a step, break a four-eyes control for lack of a second person's role, or leave nobody able to change the policy again.</p>}
          {check && !check.ok && <div className="verdict bad"><b>Not valid</b><p>{check.error}</p></div>}
          {check?.ok && <div className="verdict good"><b>Valid. Publishing would:</b><ul>{check.changes!.map(c => <li key={c}>{c}</li>)}</ul>
            {check.warnings!.map(w => <p key={w}><Pill tone="warning">note</Pill>{w}</p>)}</div>}
          <h3>Versions</h3>
          <table><thead><tr><th>Version</th><th>Published</th><th>Note</th></tr></thead>
            <tbody>{data.history.map(v => (
              <tr key={v.version}><td><b>v{v.version}</b> {v.active && <Pill tone="good">in force</Pill>}</td>
                <td className="small">{fmtDateTime(v.published)} by {nameOf(v.published_by)}</td><td className="small">{v.note}</td></tr>))}</tbody></table>
        </div>
      </div>
    </Card>
  )
}

export default function Access() {
  const { data, error, reload } = useApi<AccessData>('/access')
  const [type, setType] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const types = [...new Map(data.steps.map(s => [s.case_type, s.title])).entries()]
  const current = types.some(([id]) => id === type) ? type : types[0][0]
  const steps = data.steps.filter(s => s.case_type === current)
  const used = data.roles.filter(r => steps.some(s => s.roles.includes(r.id)))
  const roleTitle = (id: string) => data.roles.find(r => r.id === id)?.title ?? id
  const queueLabel = (id: string) => data.queues.find(q => q.id === id)?.label ?? id
  return (
    <>
      <div className="page-h"><h1>Access</h1>
        <p>Who can do what at <b>{data.tenant.name}</b>. This is the client's own policy (v{data.policy.version}): its roles, queues and people. Other clients have their own, and no role is written into code.</p></div>
      <Card title="Roles and permissions">
        <div className="scroll"><table className="matrix">
          <thead><tr><th>Role</th><th>Held by</th>{data.permissions.map(p => <th key={p.id} title={p.label}>{words(p.id)}</th>)}</tr></thead>
          <tbody>{data.roles.map(r => (
            <tr key={r.id}><td><b>{r.title}</b>{r.read_only && <Pill tone="warning">read-only</Pill>}</td><td className="small">{r.people.join(', ') || '–'}</td>
              {data.permissions.map(p => <td key={p.id} className="dot" title={`${r.title}: ${p.label}`}>{r.permissions.includes(p.id) ? <Yes /> : <No />}</td>)}</tr>))}</tbody>
        </table></div>
        <ul className="legend-list">{data.permissions.map(p => <li key={p.id}><b>{words(p.id)}</b> {p.label.split(' - ')[1] ?? p.label}</li>)}</ul>
      </Card>
      <Card title="What each role can see" action={<span className="muted small">Applied in the data layer to every list, number, search and answer</span>}>
        <table><thead><tr><th>Role</th><th>Cases they can see</th><th>Restricted cases</th></tr></thead>
          <tbody>{data.roles.map(r => (
            <tr key={r.id}><td><b>{r.title}</b></td>
              <td>{r.limited ? <Pill tone="warning">limited</Pill> : <Pill tone="good">all</Pill>}{r.sees}</td>
              <td>{r.restricted ? <Pill tone="critical">cleared</Pill> : <span className="muted">Hidden</span>}</td></tr>))}</tbody></table>
        <p className="muted small">Whatever the limit, people keep sight of cases they own or have worked on. Someone with several roles sees what any of them shows. A restricted case is hidden from everyone who is not cleared, whatever their role. An automated test signs in as every person at every client and checks each case against this table.</p>
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
        <p className="muted small">An automated test walks every person at every kind of client through every step in this table and checks that the platform allows exactly what is shown.</p>
      </Card>
      <div className="cols-2">
        <Card title="People">
          <table><thead><tr><th>Person</th><th>Roles</th><th>Queues</th><th>Sees</th></tr></thead>
            <tbody>{data.people.map(p => (
              <tr key={p.id}><td><b>{p.name}</b><div className="muted small">{p.title}</div></td>
                <td>{p.roles.map(r => <Pill key={r} tone={p.read_only ? 'warning' : 'human'}>{roleTitle(r)}</Pill>)}</td>
                <td className="small">{p.queues.map(queueLabel).join(', ') || <span className="muted">None</span>}</td>
                <td className="small">{p.sees.join('; ')}{p.restricted && <Pill tone="critical">restricted</Pill>}</td></tr>))}</tbody></table>
        </Card>
        <Card title="Queues and their members">
          <table><thead><tr><th>Queue</th><th>Members</th></tr></thead>
            <tbody>{data.queues.map(q => <tr key={q.id}><td><b>{q.label}</b><div className="muted small">{q.id}</div></td><td>{q.members.join(', ') || <span className="muted">Nobody</span>}</td></tr>)}</tbody></table>
        </Card>
      </div>
      <Editor data={data} onPublished={reload} />
    </>
  )
}
