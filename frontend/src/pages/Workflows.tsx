import { useEffect, useState } from 'react'
import { api, fmtDateTime, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../App'
import { Card, Loading, Pill } from '../components/ui'
import WorkflowDiagram from '../components/WorkflowDiagram'

type Task = { id: string; label: string; required?: boolean }
type State = { id: string; label: string; kind: string; queue: string | null; queue_label: string; sla_hours: number | null; tasks: Task[] }
type Step = { from: string; to: string; label: string; roles: string[]; requires: string[]; four_eyes: boolean; when: string }
type CT = {
  case_type: string; title: string; domain: string; version: string; initial_state: string; yaml: string
  states: State[]; transitions: Step[]; open_by_state: Record<string, number>
  sla: { clock?: string; due_days?: number }; qa: null | { sample_rate: number; dispositions?: string[]; reopen_to?: string }
  escalation: null | { raise_priority?: boolean; notify?: string }; agents: { on_create?: string[]; autonomy?: string }
  versions: { version: string; origin: string; active: boolean; open_cases: number; published_by?: string; published?: string; note?: string }[]
}
type Check = { ok: boolean; error?: string; changes?: string[]; preview?: CT }
type Ref = { roles: { id: string; people: string[] }[]; queues: { id: string; label: string }[]; requirements: { id: string; label: string }[]; facts: { id: string; label: string }[] }
type Scenario = { name: string; version: string; title: string; typology: string; params: Record<string, number> }

const clock = (h: number | null) => (h === null ? 'No stage clock' : h >= 48 ? `${Math.round(h / 24)} days` : `${h} hours`)

function Editor({ ct, onPublished }: { ct: CT; onPublished: () => void }) {
  const { can, holder } = usePeople()
  const ref = useApi<Ref>('/workflow-reference')
  const [src, setSrc] = useState(ct.yaml)
  const [note, setNote] = useState('')
  const [check, setCheck] = useState<Check | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  useEffect(() => { setSrc(ct.yaml); setCheck(null); setMsg(null) }, [ct.case_type, ct.version]) // eslint-disable-line
  const admin = can('publish_workflow')
  const run = () => api.post<Check>('/case-types/check', { source: src }).then(r => { setCheck(r); setMsg(null) }).catch(e => setMsg(e.message))
  const publish = () => api.post<{ version: string }>('/case-types/publish', { source: src, note })
    .then(r => { setMsg(`Published v${r.version}. New cases use it from now on; open cases finish on the version they started with.`); setCheck(null); setNote(''); onPublished() })
    .catch(e => setMsg(e.message))
  return (
    <Card title="Edit and publish" action={<span className="muted small">Raise the version number, check, then publish</span>}>
      <div className="editor">
        <div>
          <textarea className="code" rows={24} spellCheck={false} value={src} onChange={e => { setSrc(e.target.value); setCheck(null) }} aria-label="Case type definition" />
          <div className="toolbar">
            <button onClick={run}>Check draft</button>
            <input type="search" placeholder="What changed and why" value={note} onChange={e => setNote(e.target.value)} aria-label="Change note" />
            <button className="primary" disabled={!admin || !check?.ok} onClick={publish}
              title={!admin ? 'Publishing needs the publish workflow permission' : !check?.ok ? 'Check the draft first' : ''}>Publish new version</button>
            {!admin && <span className="muted small">Sign in as {holder('publish_workflow')} to publish.</span>}
          </div>
          {msg && <p className="msg" role="status">{msg}</p>}
        </div>
        <div>
          {!check && <p className="empty">Check the draft to see what it would change. Nothing goes live until it passes every rule: known roles and queues, no dead ends, every stage able to reach an outcome, conditions that parse.</p>}
          {check && !check.ok && <div className="verdict bad"><b>Not valid</b><p>{check.error}</p></div>}
          {check?.ok && <div className="verdict good"><b>Valid. Publishing would:</b><ul>{check.changes!.map(c => <li key={c}>{c}</li>)}</ul></div>}
          {check?.ok && check.preview && <WorkflowDiagram states={check.preview.states} steps={check.preview.transitions} initial={check.preview.initial_state} />}
          {ref.data && <details><summary><b>What a definition can refer to</b></summary>
            <h3>Roles</h3><p>{ref.data.roles.map(r => <Pill key={r.id} tone="human">{r.id}</Pill>)}<span className="muted small">These are this client's roles. Another client's list can differ.</span></p>
            <h3>Queues</h3><p>{ref.data.queues.map(q => <Pill key={q.id}>{q.id}</Pill>)}</p>
            <h3>Requirements for a step</h3><ul className="plain">{ref.data.requirements.map(r => <li key={r.id}><code>{r.id}</code> {r.label}</li>)}</ul>
            <h3>Facts for conditions</h3><ul className="plain">{ref.data.facts.map(f => <li key={f.id}><code>{f.id}</code> {f.label}</li>)}</ul>
          </details>}
        </div>
      </div>
    </Card>
  )
}

export default function Workflows() {
  const types = useApi<CT[]>('/case-types')
  const scen = useApi<Scenario[]>('/scenarios')
  const nameOf = useNameOf()
  const [sel, setSel] = useState<string | null>(null)
  const [stage, setStage] = useState<string | null>(null)
  if (!types.data) return <Loading error={types.error} />
  const ct = types.data.find(t => t.case_type === sel) ?? types.data[0]
  const st = ct.states.find(s => s.id === stage) ?? ct.states.find(s => s.id === ct.initial_state)!
  const label = (id: string) => ct.states.find(s => s.id === id)?.label ?? id
  const open = Object.values(ct.open_by_state).reduce((a, b) => a + b, 0)
  return (
    <>
      <div className="page-h"><h1>Workflows</h1><p>Each case type is a versioned definition: stages, who works them, what must be done, which steps are allowed and under what conditions. Changing one is a configuration change, checked before it goes live. Versions published here belong to this client only.</p></div>
      <div className="seg" role="group" aria-label="Case type">
        {types.data.map(t => <button key={t.case_type} aria-pressed={t.case_type === ct.case_type} onClick={() => { setSel(t.case_type); setStage(null) }}>{t.title}</button>)}
      </div>
      <Card title={`${ct.title} · v${ct.version}`} action={<span className="muted small">{ct.domain} · {open} open case{open === 1 ? '' : 's'} · select a stage for its detail</span>}>
        <WorkflowDiagram states={ct.states} steps={ct.transitions} initial={ct.initial_state} counts={ct.open_by_state} selected={st.id} onSelect={setStage} />
      </Card>
      <div className="cols-2">
        <Card title={`Stage: ${st.label}`}>
          {st.kind === 'closed' ? <p>An outcome. Cases that reach it are closed{ct.qa ? ' and may be sampled for QA' : ''}.</p> : <>
            <dl className="facts">
              <dt>Worked by</dt><dd>{st.queue_label || '–'}</dd>
              <dt>Stage clock</dt><dd>{clock(st.sla_hours)}{st.kind === 'wait' && ' (waiting on someone outside the team)'}</dd>
            </dl>
            <h3>Tasks in this stage</h3>
            {st.tasks.length === 0 ? <p className="muted">None.</p> :
              <ul className="plain">{st.tasks.map(t => <li key={t.id}>{t.label} {t.required === false ? <Pill>optional</Pill> : <Pill tone="warning">required</Pill>}</li>)}</ul>}
            <h3>Steps out of this stage</h3>
            <table><thead><tr><th>Step</th><th>Who</th><th>Controls</th></tr></thead>
              <tbody>{ct.transitions.filter(t => t.from === st.id).map((t, i) => (
                <tr key={i}><td><b>{t.label}</b><div className="muted small">to {label(t.to)}</div></td>
                  <td>{t.roles.map(r => <Pill key={r} tone="human">{words(r)}</Pill>)}</td>
                  <td>{t.four_eyes && <Pill tone="serious">four-eyes</Pill>}{t.requires.map(r => <Pill key={r} tone="warning">needs {words(r)}</Pill>)}
                    {t.when && <div className="small">Only if <code>{t.when}</code></div>}</td></tr>))}</tbody></table>
          </>}
        </Card>
        <Card title="Case type settings">
          <dl className="facts">
            <dt>Deadline</dt><dd>{ct.sla.clock ? `${ct.sla.clock}, ${ct.sla.due_days} days` : '–'}</dd>
            <dt>On a late stage</dt><dd>{ct.escalation ? `${ct.escalation.raise_priority === false ? 'Flag' : 'Raise priority and flag'} to ${words(ct.escalation.notify ?? 'team_lead')}` : 'Raise priority and flag'}</dd>
            <dt>QA sampling</dt><dd>{ct.qa ? `${Math.round(ct.qa.sample_rate * 100)}% of ${ct.qa.dispositions?.map(words).join(', ') ?? 'all'} closures${ct.qa.reopen_to ? `; a failed review reopens the case at ${label(ct.qa.reopen_to)}` : ''}` : 'Not sampled'}</dd>
            <dt>Agents</dt><dd>{ct.agents.on_create?.length ? ct.agents.on_create.map(a => <Pill key={a} tone="agent">{words(a)}</Pill>) : 'None'}</dd>
            <dt>Agent autonomy</dt><dd>{ct.agents.autonomy ?? '–'} · agents prepare, people decide</dd>
            <dt>Raised by</dt><dd>{(ct as CT & { scenarios?: string[] }).scenarios?.length ? (ct as CT & { scenarios: string[] }).scenarios.map(s => <Pill key={s}>{words(s)}</Pill>) : 'Schedule, intake or another case'}</dd>
          </dl>
          <h3>Versions</h3>
          <table><thead><tr><th>Version</th><th>Source</th><th className="num">Open cases</th><th>Published</th></tr></thead>
            <tbody>{ct.versions.map(v => (
              <tr key={v.version}><td><b>v{v.version}</b> {v.active && <Pill tone="good">active</Pill>}</td><td>{v.origin}</td><td className="num">{v.open_cases}</td>
                <td className="small">{v.published ? `${fmtDateTime(v.published)} by ${nameOf(v.published_by)}${v.note ? `: ${v.note}` : ''}` : 'Platform library'}</td></tr>))}</tbody></table>
        </Card>
      </div>
      <Editor ct={ct} onPublished={types.reload} />
      <Card title="Detection scenarios">
        {!scen.data ? <Loading error={scen.error} /> : <table>
          <thead><tr><th>Scenario</th><th>Typology</th><th>Version</th><th>Parameters</th></tr></thead>
          <tbody>{scen.data.map(s => <tr key={s.name}><td><b>{s.title}</b><div className="muted small">{s.name}</div></td><td>{s.typology}</td><td>{s.version}</td>
            <td className="small">{Object.entries(s.params).map(([k, v]) => <Pill key={k}>{k}: {v}</Pill>)}</td></tr>)}</tbody></table>}
      </Card>
    </>
  )
}
