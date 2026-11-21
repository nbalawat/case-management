import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDateTime } from '../api'
import { useApi } from '../useApi'
import { useNameOf } from '../shell/people'
import { Card, Pill } from './ui'
import type { CaseRow } from '../pages/Cases'

type Code = { code: string; label: string; weight?: number }
type Mark = { mark: string; root_cause: string; comment: string }
type Scorecard = { answers: Record<string, Mark>; score: number; pass_mark: number; by: string; at: string }
type Extras = {
  merge_candidates: CaseRow[]; can_merge: boolean; merged_into: string | null; merged_from: string[]
  reason: { code: string; label: string } | null
  qa: null | { criteria: Code[]; root_causes: Code[]; scorecard: Scorecard | null; pass_mark: number; can_score: boolean }
}
const MARKS = [['met', 'Met'], ['partly', 'Partly'], ['not_met', 'Not met']]

/** What a case carries beyond its own workflow: why it closed, what it merged with, and its QA scorecard. */
export default function CaseExtras({ caseId, tick, onChange }: { caseId: string; tick: number; onChange: (message: string) => void }) {
  const { data } = useApi<Extras>(`/cases/${caseId}/extras`, [tick])
  if (!data) return null
  const merged = data.merged_into || data.merged_from.length > 0 || data.merge_candidates.length > 0
  return (
    <>
      {data.reason && <Card title="Closing reason"><p>{data.reason.label} <span className="muted small">({data.reason.code})</span></p></Card>}
      {data.qa && <ScorecardCard caseId={caseId} qa={data.qa} onChange={onChange} />}
      {merged && <MergeCard caseId={caseId} data={data} onChange={onChange} />}
    </>
  )
}

function MergeCard({ caseId, data, onChange }: { caseId: string; data: Extras; onChange: (m: string) => void }) {
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const nameOf = useNameOf()
  const merge = (into: string) => api.post(`/cases/${caseId}/merge`, { into, note })
    .then(() => { setNote(''); onChange(`Merged into ${into}. Its alerts and requests moved across.`) }).catch(e => setError(e.message))
  return (
    <Card title="Related cases">
      {data.merged_into && <p>This case was merged into <Link to={`/cases/${data.merged_into}`}>{data.merged_into}</Link>. Its alerts and requests continue there.</p>}
      {data.merged_from.length > 0 && <p>Includes the work of {data.merged_from.map(id => <Link key={id} to={`/cases/${id}`}>{id} </Link>)}, merged into this case.</p>}
      {data.merge_candidates.length > 0 && <>
        <p className="muted small">Another open case of this type exists for the same customer. If they are one matter, merge this case into it.</p>
        {data.merge_candidates.map(o => (
          <div className="merge" key={o.id}>
            <span><Link to={`/cases/${o.id}`}><b>{o.id}</b></Link> · {o.state_label} · {o.owner ? nameOf(o.owner) : 'Unassigned'}</span>
            {data.can_merge && <button disabled={!note.trim()} title={note.trim() ? '' : 'Say why these are one matter first'} onClick={() => merge(o.id)}>Merge into {o.id}</button>}
          </div>))}
        {data.can_merge && <input aria-label="Why these are one matter" placeholder="Why are these one matter?" value={note} onChange={e => setNote(e.target.value)} maxLength={300} />}
        {!data.can_merge && <p className="muted small">The owner of the surviving case or a team lead can merge them.</p>}
      </>}
      {error && <p className="msg" role="status">{error}</p>}
    </Card>
  )
}

function ScorecardCard({ caseId, qa, onChange }: { caseId: string; qa: NonNullable<Extras['qa']>; onChange: (m: string) => void }) {
  const nameOf = useNameOf()
  const [answers, setAnswers] = useState<Record<string, Mark>>({})
  const [error, setError] = useState<string | null>(null)
  const [from, setFrom] = useState<typeof qa.scorecard | null>(null)
  if (from !== qa.scorecard) { setFrom(qa.scorecard); setAnswers(qa.scorecard?.answers ?? {}) }  // a saved scorecard comes back: show it
  const set = (code: string, change: Partial<Mark>) => setAnswers(a => ({ ...a, [code]: { ...(a[code] ?? { mark: '', root_cause: '', comment: '' }), ...change } }))
  const total = qa.criteria.reduce((n, c) => n + (c.weight ?? 1), 0)
  const earned = qa.criteria.reduce((n, c) => n + (c.weight ?? 1) * ({ met: 1, partly: 0.5 }[answers[c.code]?.mark] ?? 0), 0)
  const complete = qa.criteria.every(c => answers[c.code]?.mark && (answers[c.code].mark === 'met' || answers[c.code].root_cause))
  const live = total ? Math.round(100 * earned / total) : 0
  const save = () => api.put<Scorecard>(`/cases/${caseId}/scorecard`, { answers })
    .then(card => { setError(null); onChange(`Scorecard saved at ${card.score}%. ${card.score >= card.pass_mark ? 'The review can pass.' : 'Below the pass mark, so the review cannot pass.'}`) })
    .catch(e => setError(e.message))
  const card = qa.scorecard
  return (
    <Card title="QA scorecard" action={card ? <Pill tone={card.score >= card.pass_mark ? 'good' : 'critical'}>{card.score}% · pass mark {card.pass_mark}%</Pill> : <Pill tone="warning">not scored</Pill>}>
      {!qa.can_score && !card && <p className="empty">A QA reviewer has not scored this review yet.</p>}
      {(qa.can_score || card) && <div className="scorecard">
        {qa.criteria.map(c => {
          const a = answers[c.code]
          return (
            <div className="criterion" key={c.code}>
              <div><b>{c.label}</b> <span className="muted small">weight {c.weight ?? 1}</span></div>
              <div className="seg small" role="group" aria-label={c.label}>
                {MARKS.map(([id, label]) => <button key={id} type="button" disabled={!qa.can_score} aria-pressed={a?.mark === id} onClick={() => set(c.code, { mark: id, root_cause: id === 'met' ? '' : a?.root_cause ?? '' })}>{label}</button>)}
              </div>
              {a?.mark && a.mark !== 'met' && <div className="inline">
                <select aria-label={`Root cause for ${c.label}`} disabled={!qa.can_score} value={a.root_cause} onChange={e => set(c.code, { root_cause: e.target.value })}>
                  <option value="">Root cause…</option>{qa.root_causes.map(r => <option key={r.code} value={r.code}>{r.label}</option>)}</select>
                <input type="search" aria-label={`Comment on ${c.label}`} disabled={!qa.can_score} placeholder="Comment for the analyst" value={a.comment} onChange={e => set(c.code, { comment: e.target.value })} maxLength={500} />
              </div>}
            </div>)
        })}
        {qa.can_score && <div className="inline"><button className="primary" disabled={!complete} title={complete ? '' : 'Mark every criterion, and give a root cause for any shortfall'} onClick={save}>Save scorecard</button>
          <span className="muted small">As marked: {live}%. A review can pass only at {qa.pass_mark}% or more, and can fail at any score.</span></div>}
        {card && <p className="muted small">Scored by {nameOf(card.by)} on {fmtDateTime(card.at)}.</p>}
      </div>}
      {error && <p className="msg" role="status">{error}</p>}
    </Card>
  )
}
