import { useState } from 'react'
import { api, fmtDate } from '../../../api'
import { useNameOf } from '../../../shell/people'
import { Card, Pill } from '../../ui'
import type { Act, CaseDetail, RequestDraft } from './types'

/** Questions sent to the customer, a branch or another team, with their deadlines and the answers recorded. */
export default function RequestsCard({ d, act, draft, setDraft }: { d: CaseDetail; act: Act; draft: RequestDraft; setDraft: (r: RequestDraft) => void }) {
  const { case: c, access } = d
  const nameOf = useNameOf()
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const editable = c.open && access.can_work
  return (
    <Card title={`Information requests (${d.requests.length})`}>
      {d.requests.length === 0 && <p className="empty">None raised.</p>}
      {d.requests.map(r => (
        <div className="rfi" key={r.id}>
          <div><b>{r.recipient_label}</b> <Pill tone={r.status === 'answered' ? 'good' : new Date(r.due) < new Date() ? 'critical' : 'warning'}>
            {r.status === 'answered' ? 'answered' : new Date(r.due) < new Date() ? 'overdue' : `due ${fmtDate(r.due)}`}</Pill>
            <span className="muted small"> asked by {nameOf(r.raised_by)}, {fmtDate(r.created)}</span></div>
          <p>{r.question}</p>
          {r.status === 'answered' ? <p className="answer">{r.response}<span className="muted small"> recorded by {nameOf(r.answered_by)}, {fmtDate(r.answered!)}</span></p>
            : editable && <div className="inline">
              <input type="search" placeholder="Record the response" value={answers[r.id] ?? ''} onChange={e => setAnswers({ ...answers, [r.id]: e.target.value })} aria-label="Response" />
              <button disabled={!(answers[r.id] ?? '').trim()} onClick={() => act(() => api.post(`/requests/${r.id}/answer`, { response: answers[r.id] }), 'Response recorded.')}>Record</button></div>}
        </div>))}
      {editable && <details className="newrfi" open={!!draft.question || d.requests.length === 0}><summary>Raise a request</summary>
        <div className="inline">
          <select value={draft.recipient} onChange={e => setDraft({ ...draft, recipient: e.target.value })} aria-label="Recipient">
            {Object.entries(d.workflow.recipients).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
          <select value={draft.due_days} onChange={e => setDraft({ ...draft, due_days: Number(e.target.value) })} aria-label="Due">
            {[2, 5, 10, 20].map(n => <option key={n} value={n}>Due in {n} days</option>)}</select>
        </div>
        <textarea rows={3} placeholder="What do you need to know?" value={draft.question} onChange={e => setDraft({ ...draft, question: e.target.value })} aria-label="Question" />
        <button disabled={!draft.question.trim()} onClick={() => act(() => api.post(`/cases/${c.id}/requests`, draft).then(() => setDraft({ ...draft, question: '' })), 'Request raised.')}>Send request</button>
      </details>}
    </Card>
  )
}
