import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Circle } from 'lucide-react'
import { api, fmtDateTime } from '../../../api'
import { useNameOf, usePeople, usePersona } from '../../../shell/people'
import { Card, Pill } from '../../ui'
import type { Act, CaseDetail, Transition } from './types'

/**
 * The decision, always in view: the stage's tasks as a checklist with progress, the note and closing reason, and
 * every step the workflow offers. A blocked step says in a few words what it is waiting for.
 */
export default function DecisionRail({ d, act, note, setNote, msg }: { d: CaseDetail; act: Act; note: string; setNote: (n: string) => void; msg: string | null }) {
  const { case: c, access } = d
  const nameOf = useNameOf()
  const persona = usePersona()
  const { can } = usePeople()
  const [reason, setReason] = useState('')
  const required = d.tasks.filter(t => t.required)
  const done = required.filter(t => t.done).length
  const next = d.tasks.find(t => !t.done && t.required)
  // Closing reasons differ by outcome, so the list offers every reason and each step accepts only its own.
  const reasons = [...new Map(d.transitions.flatMap(t => t.reasons).map(r => [r.code, r])).values()]
  const why = (t: Transition) => t.blocked ?? (t.needs_note && !note.trim() ? 'Add a rationale note first'
    : t.reasons.length && !t.reasons.some(r => r.code === reason) ? (reason ? 'The chosen reason does not apply to this outcome' : 'Choose a closing reason first') : '')
  const short = (text: string) => (/^Finish required tasks/.test(text) ? `${required.length - done} task${required.length - done === 1 ? '' : 's'} left` : text)
  const f = d.filing
  return (
    <Card title="Decision" className="decision-rail">
      {f && <div className="filed"><b>{f.kind} {f.status === 'acknowledged' ? `accepted by the regulator (${f.reference})` : f.status === 'submitted' ? 'sent to the regulator' : f.status === 'rejected' ? 'rejected by the regulator' : f.problems.length ? 'prepared but not sent: it needs fixing' : 'prepared, not sent yet'}</b> · prepared by {nameOf(f.filed_by)} on {fmtDateTime(f.filed_at)}<code>{f.id} · sha256 {f.sha256.slice(0, 16)}…</code>{(can('submit_filings') || can('view_team')) && <Link to={`/filings/${f.id}`}>Open the report</Link>}</div>}
      {!c.open && !f && c.disposition !== 'merged' && <p className="empty">Closed: {c.state_label}.</p>}
      {c.open && <>
        {d.tasks.length > 0 && <fieldset className="tasks">
          <legend>Tasks in this stage{required.length > 0 && <> · {done} of {required.length} done</>}</legend>
          {required.length > 0 && <div className="task-progress" aria-hidden><div style={{ width: `${(done / required.length) * 100}%` }} /></div>}
          {d.tasks.map(t => (
            <label key={t.id} className={t.done ? 'done' : t === next ? 'next' : ''}>
              <input type="checkbox" checked={t.done} disabled={!access.can_work}
                onChange={e => act(() => api.post(`/cases/${c.id}/tasks/${t.id}`, { done: e.target.checked }), e.target.checked ? 'Task done.' : 'Task reopened.')} />
              <span>{t.label}{!t.required && <Pill>optional</Pill>}{t === next && access.can_work && <Pill tone="human">next</Pill>}
                {t.done && <small>{nameOf(t.done_by)}, {fmtDateTime(t.done_at!)}</small>}</span>
            </label>))}
        </fieldset>}
        <label className="lbl" htmlFor="note">Rationale note</label>
        <textarea id="note" rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="Required for closures and returns" />
        {reasons.length > 0 && <>
          <label className="lbl" htmlFor="reason">Closing reason</label>
          <select id="reason" value={reason} onChange={e => setReason(e.target.value)} style={{ width: '100%' }}>
            <option value="">Choose a reason (needed to close)</option>{reasons.map(r => <option key={r.code} value={r.code}>{r.label}</option>)}
          </select></>}
        <div className="actions">{d.transitions.map(t => {
          const w = why(t)
          return (
            <button key={t.to + t.label} disabled={!!w} title={w}
              className={/file|block|refund$/i.test(t.label) ? 'primary' : ''}
              onClick={() => act(() => api.post(`/cases/${c.id}/transition`, { to: t.to, note, reason: t.reasons.length ? reason : '' }), `Moved to ${t.to_label}.`)}>
              <span className="act-label">{w ? <Circle size={14} aria-hidden /> : <CheckCircle2 size={14} aria-hidden />}{t.label}{t.four_eyes && !t.blocked && <em className="tag">four-eyes</em>}</span>
              {w && <small>{short(w)}</small>}
              {t.when && <small className="why">Offered because {t.when}</small>}
            </button>)
        })}
          {d.transitions.length === 0 && <p className="empty">No actions from this stage.</p>}
        </div>
        <p className="muted small">Acting as {persona?.name} ({persona?.title}).</p>
      </>}
      {msg && <p className="msg" role="status">{msg}</p>}
    </Card>
  )
}
