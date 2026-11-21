import { useState } from 'react'
import { api, fmtDateTime } from '../../../api'
import { useApi } from '../../../useApi'
import { useNameOf, usePeople } from '../../../shell/people'
import { Card, Loading, Pill } from '../../ui'
import FormRenderer from '../../forms/FormRenderer'
import type { Answers, CaseForm } from '../../forms/types'

const STATUS: Record<CaseForm['status'], [string, string]> = { not_started: ['neutral', 'not started'], draft: ['warning', 'in progress'], complete: ['good', 'complete'] }

/** One form on the case. Keyed by form and status, so a save that changes the status starts from the stored answers. */
function OneForm({ entry, caseId, onSaved }: { entry: CaseForm; caseId: string; onSaved: () => void }) {
  const { can } = usePeople()
  const nameOf = useNameOf()
  const [answers, setAnswers] = useState<Answers>(entry.data)
  const [problems, setProblems] = useState<Record<string, string>>({})
  const [msg, setMsg] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const editable = !entry.locked && entry.status !== 'complete' && can('work')
  const save = (complete: boolean) => {
    fetchSave(caseId, entry.form.id, answers, complete).then(r => {
      if (r.ok) { setProblems({}); setMsg(complete ? 'Completed.' : 'Saved.'); onSaved() } else { const n = Object.keys(r.fields ?? {}).length
        setProblems(r.fields ?? {}); setMsg(n ? `${n} answer${n === 1 ? ' needs' : 's need'} attention.` : r.detail ?? null)
      }
    })
  }
  const reopen = () => api.post(`/cases/${caseId}/forms/${entry.form.id}/reopen`, { reason }).then(onSaved).catch(e => setMsg(e.message))
  const [tone, label] = STATUS[entry.status]
  return (
    <Card title={entry.form.title} action={<span className="small"><Pill tone={tone}>{label}</Pill> <span className="muted">v{entry.form.version}</span></span>}>
      {entry.form.description && <p className="muted small">{entry.form.description}</p>}
      {entry.locked && <p className="muted small">{entry.locked}: the form is read-only here.</p>}
      <FormRenderer form={entry.form} answers={answers} problems={problems} readOnly={!editable} onChange={a => { setAnswers(a); setMsg(null) }} />
      {editable && (
        <div className="toolbar">
          <button onClick={() => save(false)}>Save draft</button>
          <button className="primary" onClick={() => save(true)}>Complete</button>
          {Object.keys(entry.missing).length > 0 && entry.status !== 'not_started' && <span className="muted small">{Object.keys(entry.missing).length} answer(s) still needed to complete</span>}
        </div>
      )}
      {entry.status === 'complete' && !entry.locked && can('work') && (
        <div className="toolbar">
          <input type="search" placeholder="Why it needs changing" value={reason} onChange={e => setReason(e.target.value)} aria-label="Reason to reopen" />
          <button disabled={!reason.trim()} onClick={reopen}>Reopen</button>
        </div>
      )}
      {msg && <p className="msg" role="status">{msg}</p>}
      {entry.updated && <p className="muted small">
        {entry.completed_at ? `Completed ${fmtDateTime(entry.completed_at)} by ${nameOf(entry.completed_by ?? '')}` : `Last saved ${fmtDateTime(entry.updated)} by ${nameOf(entry.updated_by ?? '')}`}
      </p>}
    </Card>
  )
}

type SaveResult = { ok: boolean; detail?: string; fields?: Record<string, string> }

/** PUT the answers; a 422 carries per-field problems, which the form shows in place. */
async function fetchSave(caseId: string, formId: string, data: Answers, complete: boolean): Promise<SaveResult> {
  try {
    await api.put(`/cases/${caseId}/forms/${formId}`, { data, complete })
    return { ok: true }
  } catch (e) {
    const message = (e as Error).message
    const fields = Object.fromEntries(message.replace(/^Some answers need attention: /, '').split('; ').map(p => p.split(': ')).filter(p => p.length === 2))
    return { ok: false, detail: message, fields }
  }
}

/** Every form the case offers. */
export default function FormsCard({ caseId }: { caseId: string }) {
  const forms = useApi<CaseForm[]>(`/cases/${caseId}/forms`)
  if (!forms.data) return <Loading error={forms.error} />
  if (forms.data.length === 0) return <Card><p className="empty">This kind of case has no forms. Forms are built under Administration, Forms.</p></Card>
  return <>{forms.data.map(f => <OneForm key={`${f.form.id}:${f.status}:${f.updated}`} entry={f} caseId={caseId} onSaved={forms.reload} />)}</>
}
