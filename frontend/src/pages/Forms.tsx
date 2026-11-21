import { useState } from 'react'
import { Download } from 'lucide-react'
import { api, fmtDateTime, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../shell/people'
import { Card, Loading, Pill } from '../components/ui'
import FormBuilder from '../components/forms/FormBuilder'
import FormRenderer from '../components/forms/FormRenderer'
import { nextVersion, type Answers, type Form, type FormCheck, type FormDetail, type FormReference, type FormSummary } from '../components/forms/types'

const draftOf = (f: Form): Form => ({ id: f.id, title: f.title, version: f.version, description: f.description, case_types: f.case_types, stages: f.stages, sections: f.sections })

/** One form: outline and properties on the left, the live form on the right, then check and publish. Keyed by form and version. */
function Builder({ detail, reference, onPublished }: { detail: FormDetail; reference: FormReference; onPublished: (m: string) => void }) {
  const { can, holder } = usePeople()
  const nameOf = useNameOf()
  const admin = can('publish_workflow')
  const [form, setForm] = useState<Form>(draftOf(detail))
  const [sel, setSel] = useState<{ s: number; f: number } | null>(null)
  const [preview, setPreview] = useState<Answers>({})
  const [check, setCheck] = useState<FormCheck | null>(null)
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [view, setView] = useState<'preview' | 'schema'>('preview')
  const drafting = form.version !== detail.version
  const change = (f: Form) => { setForm(f); setCheck(null) }
  const run = () => api.post<FormCheck>('/forms/check', { form }).then(c => { setCheck(c); setErr(null) }).catch(e => setErr(e.message))
  const publish = () => api.post<{ version: string }>('/forms/publish', { form, note })
    .then(r => onPublished(`Published v${r.version}. Cases fill it in from now on; answers already given stay on the version they were given against.`))
    .catch(e => setErr(e.message))
  const types = reference.case_types
  const stagesFor = types.filter(t => form.case_types.includes(t.id)).flatMap(t => t.stages)
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter(x => x !== v) : [...list, v])
  return (
    <>
      <Card title={`${form.title} · v${form.version}${drafting ? ' (draft)' : ''}`} action={
        <span className="toolbar dt-tools">
          <button onClick={() => api.download(`/forms/${detail.id}/schema?version=${detail.version}`)} title="JSON Schema 2020-12, for any JSON Schema form renderer or validator"><Download size={14} /> JSON Schema</button>
        </span>}>
        <p className="muted">{form.description}</p>
        <div className="dt-settings">
          <label className="fb-wide"><span>Offered on</span>
            <span className="fb-chips">{types.map(t => (
              <button key={t.id} aria-pressed={form.case_types.includes(t.id)} disabled={!drafting} onClick={() => change({ ...form, case_types: toggle(form.case_types, t.id), stages: [] })}>{t.title}</button>
            ))}</span></label>
          {stagesFor.length > 0 && <label className="fb-wide"><span>Filled in during (none chosen: any open stage)</span>
            <span className="fb-chips">{stagesFor.map(st => (
              <button key={st.id} aria-pressed={form.stages.includes(st.id)} disabled={!drafting} onClick={() => change({ ...form, stages: toggle(form.stages, st.id) })}>{st.label}</button>
            ))}</span></label>}
        </div>
        <div className="fb-split">
          <FormBuilder form={form} reference={reference} selected={sel} onSelect={setSel} onChange={change} readOnly={!drafting || !admin} />
          <div className="fb-preview">
            <div className="seg small" role="group" aria-label="Right pane">
              <button aria-pressed={view === 'preview'} onClick={() => setView('preview')}>Live form</button>
              <button aria-pressed={view === 'schema'} onClick={() => setView('schema')}>JSON Schema</button>
            </div>
            {view === 'preview'
              ? <><p className="muted small">Fill it in to see conditional questions appear. Nothing is saved.</p><FormRenderer form={form} answers={preview} onChange={setPreview} /></>
              : <pre className="dt-yaml">{JSON.stringify(check?.json_schema ?? detail.json_schema, null, 2)}</pre>}
          </div>
        </div>
        <div className="toolbar">
          {!drafting && admin && <button className="primary" onClick={() => change({ ...form, version: nextVersion(detail.version) })}>Edit as v{nextVersion(detail.version)}</button>}
          {!admin && <span className="muted small">Sign in as {holder('publish_workflow')} to change forms.</span>}
          {drafting && <>
            <button onClick={run}>Check draft</button>
            <input type="search" placeholder="What changed and why" value={note} onChange={e => setNote(e.target.value)} aria-label="Change note" />
            <button className="primary" disabled={!check?.ok || !admin} onClick={publish} title={!check?.ok ? 'Check the draft first' : ''}>Publish v{form.version}</button>
            <button onClick={() => { setForm(draftOf(detail)); setCheck(null); setSel(null) }}>Discard</button>
          </>}
        </div>
        {err && <p className="msg" role="alert">{err}</p>}
        {check && !check.ok && <div className="verdict bad" role="alert"><b>Not valid</b><p>{check.error}</p></div>}
        {check?.ok && <div className="verdict good"><b>Valid. Publishing would:</b><ul>{check.changes!.map(c => <li key={c}>{c}</li>)}</ul></div>}
      </Card>
      <Card title="Versions">
        <table><thead><tr><th>Version</th><th>Source</th><th>Published</th></tr></thead>
          <tbody>{detail.versions.map(v => (
            <tr key={v.version}><td><b>v{v.version}</b> {v.active && <Pill tone="good">active</Pill>}</td><td>{v.origin}</td>
              <td className="small">{v.published ? `${fmtDateTime(v.published)} by ${nameOf(v.published_by ?? '')}${v.note ? `: ${v.note}` : ''}` : 'Platform library'}</td></tr>))}</tbody></table>
      </Card>
    </>
  )
}

export default function Forms() {
  const list = useApi<FormSummary[]>('/forms')
  const reference = useApi<FormReference>('/forms/reference')
  const [sel, setSel] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const current = sel ?? list.data?.[0]?.id ?? null
  const detail = useApi<FormDetail>(current ? `/forms/${current}` : null)
  if (!list.data || !reference.data) return <Loading error={list.error ?? reference.error} />
  return (
    <>
      <div className="page-h"><h1>Forms</h1>
        <p>Structured questions a case asks at a stage: a SAR decision record, an enhanced due diligence questionnaire, a fraud claim intake.
          Built here without code, checked as JSON Schema, versioned per client. Answers are kept with the case and its audit trail.</p></div>
      <div className="dt-list" role="list">
        {list.data.map(f => (
          <button key={f.id} role="listitem" aria-pressed={f.id === current} onClick={() => { setSel(f.id); setMsg(null) }}>
            <b>{f.title}</b>
            <span className="small muted">{f.fields} questions in {f.sections} section{f.sections === 1 ? '' : 's'}</span>
            <span className="small">{f.case_types.map(words).join(', ') || 'Not offered on any case type'} <span className="muted">v{f.version}</span></span>
          </button>
        ))}
      </div>
      {msg && <p className="verdict good" role="status">{msg}</p>}
      {!detail.data || detail.data.id !== current ? <Loading error={detail.error} /> :
        <Builder key={`${detail.data.id}@${detail.data.version}`} detail={detail.data} reference={reference.data}
          onPublished={m => { setMsg(m); list.reload(); detail.reload() }} />}
    </>
  )
}
