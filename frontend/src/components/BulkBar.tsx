import { useEffect, useState } from 'react'
import { api } from '../api'

type Step = { to: string; label: string; needs_note: boolean; needs_reason: boolean; reasons: { code: string; label: string }[] }
type Options = { cases: number; can_claim: boolean; can_reassign: boolean; transitions: Step[]; owners: { id: string; name: string }[] }
type Result = { done: number; refused: number; results: { id: string; ok: boolean; message: string }[] }

/** One step on every selected case. Each case passes the same checks it would alone, and the ones that refuse say why. */
export default function BulkBar({ ids, onDone, onClear }: { ids: string[]; onDone: () => void; onClear: () => void }) {
  const [options, setOptions] = useState<Options | null>(null)
  const [to, setTo] = useState('')
  const [note, setNote] = useState('')
  const [reason, setReason] = useState('')
  const [tick, setTick] = useState(true)
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState<string | null>(null)
  const key = ids.join(',')
  const [chosenFor, setChosenFor] = useState(key)
  if (chosenFor !== key) { setChosenFor(key); setTo(''); setReason('') }  // a different selection: the step chosen for the last one does not carry over
  useEffect(() => {
    api.post<Options>('/cases/bulk/options', { case_ids: ids }).then(setOptions).catch(e => setError(e.message))
  }, [key]) // eslint-disable-line react-hooks/exhaustive-deps
  const run = (body: Record<string, unknown>) => api.post<Result>('/cases/bulk', { case_ids: ids, ...body })
    .then(r => { setResult(r); setError(null); setNote(''); if (r.refused === 0) setTimeout(onDone, 1200) }).catch(e => setError(e.message))
  const step = options?.transitions.find(t => t.to === to)
  const ready = step && (!step.needs_note || note.trim()) && (!step.needs_reason || step.reasons.some(r => r.code === reason))
  return (
    <div className="bulk" role="region" aria-label="Bulk actions">
      <b>{ids.length} selected</b>
      {options?.can_claim && <button onClick={() => run({ action: 'claim' })}>Claim</button>}
      {options?.can_reassign && options.owners.length > 0 && <select value="" aria-label="Reassign selected" onChange={e => e.target.value && run({ action: 'reassign', owner: e.target.value, note: 'Reassigned in bulk' })}>
        <option value="">Reassign to…</option>{options.owners.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select>}
      {options && options.transitions.length > 0 ? <>
        <select value={to} aria-label="Step for selected cases" onChange={e => { setTo(e.target.value); setReason('') }}>
          <option value="">Move all…</option>{options.transitions.map(t => <option key={t.to} value={t.to}>{t.label}</option>)}</select>
        {step?.needs_reason && <select value={reason} aria-label="Closing reason" onChange={e => setReason(e.target.value)}>
          <option value="">Closing reason…</option>{step.reasons.map(r => <option key={r.code} value={r.code}>{r.label}</option>)}</select>}
        {step && <input type="search" aria-label="Rationale note" placeholder={step.needs_note ? 'Rationale note (required)' : 'Note (optional)'} value={note} onChange={e => setNote(e.target.value)} />}
        {step && <label className="check"><input type="checkbox" checked={tick} onChange={e => setTick(e.target.checked)} /> I completed the stage tasks</label>}
        {step && <button className="primary" disabled={!ready} onClick={() => run({ action: 'transition', to, note, reason, tick_tasks: tick })}>Apply to {ids.length}</button>}
      </> : options && <span className="muted small">These cases share no step you can take together.</span>}
      <button className="ghost" onClick={onClear}>Clear</button>
      {error && <p className="msg" role="status">{error}</p>}
      {result && <div className="bulk-result" role="status">
        <b>{result.done} done{result.refused ? `, ${result.refused} refused and left as they were` : ''}.</b>
        {result.refused > 0 && <ul>{result.results.filter(r => !r.ok).slice(0, 8).map(r => <li key={r.id}><code>{r.id}</code> {r.message}</li>)}</ul>}
        {result.refused > 0 && <button className="ghost" onClick={onDone}>Done</button>}
      </div>}
    </div>
  )
}
