import { useState } from 'react'
import { api, fmtDate, fmtDateTime } from '../../api'
import { useNameOf, usePeople } from '../../App'
import { Card, Pill } from '../ui'
import { BAND, type Risk } from './types'

/** The customer's rating: what the client's model says and why, any override, and who must approve a lower one. */
export default function RiskCard({ partyId, risk, onChange }: { partyId: string; risk: Risk; onChange: () => void }) {
  const nameOf = useNameOf()
  const { holder } = usePeople()
  const [form, setForm] = useState<null | { band: string; reason: string; note: string }>(null)
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const a = risk.assessment
  const call = (path: string, body: unknown, ok: string) => api.post(`/customers/${partyId}/risk/${path}`, body)
    .then(() => { setMsg(ok); setForm(null); setNote(''); onChange() }).catch(e => setMsg(e.message))
  const top = Math.max(100, a?.score ?? 0)
  const o = a?.override
  const lowering = !!(form && a && risk.bands.findIndex(b => b.code === form.band) < risk.bands.findIndex(b => b.code === a.band))
  return (
    <Card title="Risk rating" action={<span className="inline tight">
      <Pill tone={BAND[risk.rating]}>{risk.rating}</Pill>
      {risk.can_assess && <button onClick={() => call('assess', {}, 'Assessed under the current model.')}>{a ? 'Reassess' : 'Assess now'}</button>}</span>}>
      {!a ? <p className="empty">Rated {risk.rating} when onboarded. Not yet assessed under this client's risk model.</p> : <>
        <div className="scale" role="img" aria-label={`Score ${a.score}, which the model rates ${a.band}`}>
          {risk.bands.map((b, i) => {
            const end = risk.bands[i + 1]?.from_score ?? top
            return <div key={b.code} className={`band ${b.code}`} style={{ left: `${(100 * b.from_score) / top}%`, width: `${(100 * (end - b.from_score)) / top}%` }}><span>{b.label} from {b.from_score}</span></div>
          })}
          <div className="marker" style={{ left: `${Math.min(100, (100 * a.score) / top)}%` }}><b>{a.score}</b></div>
        </div>
        <p className="small muted">Model rating <b>{a.band}</b> from a score of {a.score}, assessed {fmtDate(a.at)} by {a.by === 'kyc-scheduler' ? 'the scheduled reassessment' : nameOf(a.by)}. Reviewed every {risk.review_months} months at the current rating.
          {risk.stale && <> <Pill tone="warning">The risk model has changed since; reassess</Pill></>}</p>
        <table className="factors">
          <thead><tr><th>Factor</th><th>Evidence</th><th className="num">Points</th></tr></thead>
          <tbody>{a.factors.filter(f => f.applies).map(f => <tr key={f.code}><td><b>{f.label}</b></td><td>{f.evidence}</td><td className="num">+{f.points}</td></tr>)}
            {a.factors.every(f => !f.applies) && <tr><td colSpan={3} className="muted">No factor applies.</td></tr>}</tbody>
        </table>
        <details><summary className="small">{a.factors.filter(f => !f.applies).length} factors tested and not met</summary>
          <ul className="small muted">{a.factors.filter(f => !f.applies).map(f => <li key={f.code}>{f.label} ({f.points} points)</li>)}</ul></details>
        {o && <div className={`override ${o.status}`}>
          <b>{o.status === 'pending' ? `Lower rating of ${o.band} proposed, waiting for approval` : o.status === 'applied' ? `Overridden to ${o.band}` : `Override to ${o.band} ${o.status}`}</b>
          <div>{o.reason}. “{o.note}” <span className="muted small">{nameOf(o.by)}, {fmtDateTime(o.at)}</span></div>
          {o.decided_by && <div className="small">{o.status === 'refused' ? 'Refused' : 'Approved'} by {nameOf(o.decided_by)}{o.decision_note ? `: “${o.decision_note}”` : ''}</div>}
          {o.status === 'pending' && (risk.can_decide ? <div className="inline">
            <input type="search" aria-label="Decision note" placeholder="Note (needed to refuse)" value={note} onChange={e => setNote(e.target.value)} />
            <button className="primary" onClick={() => call('decision', { approve: true, note }, `Approved. The rating is now ${o.band}.`)}>Approve</button>
            <button disabled={!note.trim()} onClick={() => call('decision', { approve: false, note }, 'Refused. The rating stands.')}>Refuse</button>
          </div> : <div className="small muted">A lower rating takes effect only when someone else approves it: {holder('approve_risk')}.</div>)}
          {o.status === 'applied' && risk.can_assess && <button className="ghost" onClick={() => call('withdraw', { why: 'Override no longer needed' }, 'Override withdrawn.')}>Withdraw the override</button>}
        </div>}
        {risk.can_override && !form && <button onClick={() => setForm({ band: risk.bands.find(b => b.code !== a.band)!.code, reason: '', note: '' })}>Set a different rating…</button>}
        {form && <div className="override-form">
          <div className="inline">
            <select aria-label="Rating" value={form.band} onChange={e => setForm({ ...form, band: e.target.value })}>
              {risk.bands.filter(b => b.code !== a.band).map(b => <option key={b.code} value={b.code}>{b.label}</option>)}</select>
            <select aria-label="Reason" value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })}>
              <option value="">Reason…</option>{risk.reasons.map(r => <option key={r.code} value={r.code}>{r.label}</option>)}</select>
          </div>
          <textarea rows={2} aria-label="Explanation" placeholder="Explain the override. Audit will read this." value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} />
          <div className="inline"><button className="primary" disabled={!form.reason || form.note.trim().length < 10}
            onClick={() => call('override', form, lowering ? 'Proposed. It waits for a second person.' : `Rating raised to ${form.band}.`)}>{lowering ? 'Propose for approval' : 'Raise the rating now'}</button>
            <button onClick={() => setForm(null)}>Cancel</button>
            <span className="muted small">{lowering ? 'Lowering a rating needs a second person.' : 'Raising a rating takes effect at once and triggers a review.'}</span></div>
        </div>}
        {risk.history.length > 1 && <details><summary className="small">{risk.history.length} assessments on record</summary>
          <ul className="small">{risk.history.map(h => <li key={h.id}>{fmtDate(h.at)}: score {h.score}, {h.band}{h.final_band !== h.band ? `, set to ${h.final_band}` : ''}{h.override ? ` (override ${h.override})` : ''} · {nameOf(h.by)}</li>)}</ul></details>}
      </>}
      {msg && <p className="msg" role="status">{msg}</p>}
    </Card>
  )
}
