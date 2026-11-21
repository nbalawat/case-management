import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDateTime, fmtMoney, hoursUntil, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../shell/people'
import { holdWhat } from './holdWhat'
import { Card, Pill } from './ui'

export type Hold = {
  id: string; case_id: string; party_id: string; target: string; target_label: string; target_id: string; amount: number; reason: string; kind: string; note: string
  status: string; placed_by: string; placed_at: string; review_by: string | null; overdue: boolean; two_person: boolean
  challenge: null | { method_label: string; status: string; requested_at: string; completed_at: string | null; note: string }
  proposal: null | { outcome: string; note: string; by: string; at: string }
  decided_by: string | null; decided_at: string | null; decision_note: string
  history: { at: string; by: string; what: string; note: string }[]
}
type View = {
  holds: Hold[]; can_place: boolean; can_work: boolean; can_decide: boolean; me: string
  reasons: { code: string; label: string }[]; targets: { id: string; label: string }[]; methods: { id: string; label: string }[]; accounts: string[]
}
const TONE: Record<string, string> = { active: 'warning', released: 'good', blocked: 'critical', rejected: 'serious' }
const OUTCOME: Record<string, string> = { release: 'Release', block: 'Block', reject: 'Reject and send back' }

/** One hold with everything that can be done to it by this person now. */
export function HoldRow({ h, view, onChange, report }: { h: Hold; view: Pick<View, 'can_work' | 'can_decide' | 'me' | 'methods'>; onChange: (m: string) => void; report?: boolean }) {
  const nameOf = useNameOf()
  const { holder } = usePeople()
  const [note, setNote] = useState('')
  const [method, setMethod] = useState(view.methods[0]?.id ?? '')
  const [err, setErr] = useState<string | null>(null)
  const call = (path: string, body: unknown, ok: string) => api.post(`/holds/${h.id}/${path}`, body).then(() => { setNote(''); setErr(null); onChange(ok) }).catch(e => setErr(e.message))
  const left = hoursUntil(h.review_by)
  const active = h.status === 'active'
  return (
    <div className={`hold ${h.status}`}>
      <div className="hold-h"><b>{holdWhat(h)}</b>{h.amount > 0 && <span className="num">{fmtMoney(h.amount)}</span>}
        <Pill tone={TONE[h.status]}>{words(h.status)}</Pill>
        {active && h.overdue && <Pill tone="critical">Review overdue</Pill>}
        {active && !h.overdue && left !== null && <span className="muted small">review within {left < 1 ? 'the hour' : `${left}h`}</span>}</div>
      <div className="small">{h.reason}. {h.note} <span className="muted">{nameOf(h.placed_by)}, {fmtDateTime(h.placed_at)}</span></div>
      {h.challenge && <div className="small">Step-up check ({h.challenge.method_label.toLowerCase()}): <Pill tone={h.challenge.status === 'passed' ? 'good' : h.challenge.status === 'failed' ? 'critical' : 'warning'}>
        {h.challenge.status === 'sent' ? 'waiting for the customer' : h.challenge.status}</Pill> {h.challenge.note}</div>}
      {!active && <div className="small">{words(h.status)} by {nameOf(h.decided_by ?? '')}, {h.decided_at && fmtDateTime(h.decided_at)}. “{h.decision_note}”</div>}
      {active && h.proposal && <div className="override pending">
        <b>{OUTCOME[h.proposal.outcome]} proposed, waiting for a second person</b>
        <div>“{h.proposal.note}” <span className="muted small">{nameOf(h.proposal.by)}, {fmtDateTime(h.proposal.at)}</span></div>
        {view.can_decide && h.proposal.by !== view.me ? <div className="inline">
          <input type="search" aria-label="Decision note" placeholder="Note (needed to refuse)" value={note} onChange={e => setNote(e.target.value)} />
          <button className="primary" onClick={() => call('decision', { approve: true, note }, `Approved: ${words(h.proposal!.outcome)}.`)}>Approve</button>
          <button disabled={!note.trim()} onClick={() => call('decision', { approve: false, note }, 'Refused. The hold stays.')}>Refuse</button>
        </div> : <div className="small muted">{h.proposal.by === view.me ? 'You proposed this, so someone else decides.' : `${holder('sanctions_decisions')} can decide.`}</div>}
      </div>}
      {active && !h.proposal && view.can_work && <div className="hold-act">
        {h.kind !== 'sanctions' && h.challenge?.status !== 'sent' && <span className="inline tight">
          <select aria-label="Step-up check" value={method} onChange={e => setMethod(e.target.value)}>{view.methods.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}</select>
          <button onClick={() => call('challenge', { method }, 'Step-up check sent to the customer.')}>Send step-up check</button></span>}
        {h.challenge?.status === 'sent' && <span className="inline tight">
          <button onClick={() => call('challenge/result', { passed: true, note }, 'Recorded: the customer passed.')}>Customer passed</button>
          <button onClick={() => call('challenge/result', { passed: false, note }, 'Recorded: the customer failed.')}>Customer failed</button></span>}
        <div className="inline">
          <input type="search" aria-label="Why" placeholder={h.two_person ? 'Why (a second person will read this)' : 'Why'} value={note} onChange={e => setNote(e.target.value)} />
          {Object.entries(OUTCOME).filter(([k]) => k !== 'reject' || h.target === 'payment').map(([k, label]) =>
            <button key={k} disabled={note.trim().length < 10} onClick={() => call('outcome', { outcome: k, note }, h.two_person ? `${label} proposed. It waits for a second person.` : `${label}: done.`)}>{h.two_person ? `Propose: ${label.toLowerCase()}` : label}</button>)}
        </div>
      </div>}
      {report && !active && h.kind === 'sanctions' && h.status !== 'released' && <div className="small muted">A {h.status} sanctions hold is reported to the authority. See <Link to="/sanctions">Sanctions</Link> for what is owed.</div>}
      {err && <p className="msg" role="alert">{err}</p>}
    </div>
  )
}

/** Holds on this case: what is stopped, who stopped it, and how each one ended. */
export default function HoldsCard({ caseId, tick, onChange }: { caseId: string; tick: number; onChange: (m: string) => void }) {
  const { data, reload } = useApi<View>(`/cases/${caseId}/holds`, [tick])
  const [form, setForm] = useState<null | { target: string; target_id: string; reason: string; note: string }>(null)
  const [err, setErr] = useState<string | null>(null)
  if (!data || (data.holds.length === 0 && !data.can_place)) return null
  const done = (m: string) => { reload(); onChange(m) }
  const place = () => form && api.post(`/cases/${caseId}/holds`, form).then(() => { setForm(null); setErr(null); done('Hold placed.') }).catch(e => setErr(e.message))
  return (
    <Card title={`Holds (${data.holds.filter(h => h.status === 'active').length} active)`} action={data.can_place && !form
      ? <button onClick={() => setForm({ target: 'account', target_id: data.accounts[0] ?? '', reason: data.reasons[0]?.code ?? '', note: '' })}>Place a hold…</button> : undefined}>
      {form && <div className="override-form">
        <div className="inline wrap">
          <select aria-label="What to hold" value={form.target} onChange={e => setForm({ ...form, target: e.target.value, target_id: e.target.value === 'account' ? data.accounts[0] ?? '' : '' })}>
            {data.targets.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}</select>
          {form.target === 'payment'
            ? <input type="search" aria-label="Payment id" placeholder="Payment id, from the transactions list" value={form.target_id} onChange={e => setForm({ ...form, target_id: e.target.value })} />
            : <select aria-label="Account" value={form.target_id} onChange={e => setForm({ ...form, target_id: e.target.value })}>
              {form.target !== 'account' && <option value="">All accounts</option>}{data.accounts.map(a => <option key={a} value={a}>{a}</option>)}</select>}
          <select aria-label="Reason" value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })}>{data.reasons.map(r => <option key={r.code} value={r.code}>{r.label}</option>)}</select>
        </div>
        <div className="inline"><input type="search" aria-label="Note" placeholder="Note" value={form.note} onChange={e => setForm({ ...form, note: e.target.value })} />
          <button className="primary" onClick={place}>Place hold</button><button onClick={() => setForm(null)}>Cancel</button></div>
        {err && <p className="msg" role="alert">{err}</p>}
      </div>}
      {data.holds.length === 0 ? <p className="empty">Nothing is held on this case.</p> : data.holds.map(h => <HoldRow key={h.id} h={h} view={data} onChange={done} report />)}
      {data.holds.some(h => h.status === 'active') && <p className="muted small">The case cannot close while a hold is active.</p>}
    </Card>
  )
}
