import { useState } from 'react'
import { api, fmtDate, fmtDateTime, fmtMoney, daysUntil, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf } from '../shell/people'
import { Card, Pill } from './ui'

export type Dispute = {
  id: string; case_id: string; party_id: string; txn_ids: string[]; amount: number; rail: string; reason: string; status: string; outcome: string | null
  reported_at: string; opened_by: string; credit_due: string | null; credit_at: string | null; credit_amount: number; credit_final: boolean; credit_overdue: boolean; credit_late: boolean
  resolve_due: string | null; resolve_overdue: boolean; recovery_name: string; recovery_ref: string; recovery_at: string | null; recovery_due: string | null
  recovery_open: boolean; recovery_by_request: boolean; recovered: number; customer_liability: number; loss: number; exposure: number; closed_at: string | null
  history: { at: string; by: string; what: string; note: string; amount: number | null }[]
}
type View = {
  disputes: Dispute[]; can_work: boolean; can_open: boolean; reasons: { code: string; label: string }[]
  payments: { id: string; ts: string; amount: number; channel: string; counterparty: string; disputed: boolean }[]
}
const OUT: Record<string, [string, string]> = {
  recovered: ['good', 'Recovered in full'], partly_recovered: ['warning', 'Partly recovered'], written_off: ['critical', 'Written off'],
  customer_liable: ['neutral', 'Customer liable'], withdrawn: ['neutral', 'Withdrawn'],
}
const STATUS: Record<string, string> = { open: 'Claim open', credited: 'Customer credited', recovery: 'Recovery under way' }

/** A deadline as people read it: the date, and how close it is while it still matters. */
export function Clock({ due, done, overdue, label }: { due: string | null; done?: string | null; overdue?: boolean; label: string }) {
  if (!due) return <span className="muted">No {label.toLowerCase()} deadline on this kind of payment</span>
  if (done) return <span>{label} {fmtDate(done)}</span>
  const days = daysUntil(due)
  return overdue ? <Pill tone="critical">{label} overdue since {fmtDate(due)}</Pill> : <span>{label} by {fmtDate(due)}{days !== null && days <= 5 && <Pill tone="warning">{days}d left</Pill>}</span>
}

function One({ d, canWork, onChange }: { d: Dispute; canWork: boolean; onChange: (m: string) => void }) {
  const nameOf = useNameOf()
  const [amount, setAmount] = useState(String(d.credit_amount ? Math.max(0, d.amount - d.recovered) : d.amount))
  const [text, setText] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const step = (body: Record<string, unknown>, ok: string) => api.post(`/disputes/${d.id}`, body).then(() => { setText(''); setErr(null); onChange(ok) }).catch(e => setErr(e.message))
  const open = d.status !== 'closed'
  return (
    <div className={`hold ${open ? 'active' : 'released'}`}>
      <div className="hold-h"><b>{fmtMoney(d.amount)} by {d.rail}</b><span className="muted small">{d.txn_ids.length} payment{d.txn_ids.length === 1 ? '' : 's'} · {d.reason}</span>
        {open ? <Pill tone="human">{STATUS[d.status]}</Pill> : <Pill tone={OUT[d.outcome!]?.[0]}>{OUT[d.outcome!]?.[1] ?? words(d.outcome!)}</Pill>}</div>
      <dl className="facts dense">
        <dt>Credit to customer</dt><dd>{d.credit_amount ? <>{fmtMoney(d.credit_amount)} {d.credit_final ? 'final' : 'provisional'}, {fmtDate(d.credit_at!)}{d.credit_late && <Pill tone="serious">after the deadline</Pill>}</>
          : <Clock due={d.credit_due} overdue={d.credit_overdue} label="Provisional credit" />}</dd>
        <dt className="cap">{d.recovery_name}</dt><dd>{d.recovery_at ? <>{d.recovery_ref}, sent {fmtDate(d.recovery_at)} · {fmtMoney(d.recovered)} back so far</>
          : d.recovery_by_request ? <span className="muted">By request to the other bank; no network deadline</span>
            : d.recovery_open ? <>Can be sent until {fmtDate(d.recovery_due!)}</> : <Pill tone="serious">Window closed {fmtDate(d.recovery_due!)}</Pill>}</dd>
        <dt>Resolution</dt><dd>{open ? <Clock due={d.resolve_due} overdue={d.resolve_overdue} label="Resolve" /> : <>Closed {fmtDate(d.closed_at!)} · loss {fmtMoney(d.loss)}</>}</dd>
      </dl>
      {open && canWork && <div className="hold-act">
        <div className="inline wrap">
          <input type="number" aria-label="Amount" min={0} step="0.01" value={amount} onChange={e => setAmount(e.target.value)} />
          <input type="search" aria-label="Reference or note" placeholder={d.recovery_at ? 'Note' : `Reference of the ${d.recovery_name}, or a note`} value={text} onChange={e => setText(e.target.value)} />
        </div>
        <div className="inline wrap">
          {!d.credit_final && <button onClick={() => step({ step: 'credit', amount: Number(amount), note: text }, 'Customer credited provisionally.')}>Provisional credit</button>}
          {!d.credit_final && <button onClick={() => step({ step: 'final_credit', amount: Number(amount), note: text }, 'Credit made final.')}>Final credit</button>}
          {!d.recovery_at && (d.recovery_open || d.recovery_by_request) && <button disabled={!text.trim()} onClick={() => step({ step: 'recovery', reference: text }, `The ${d.recovery_name} is on record.`)}>Record the {d.recovery_name}</button>}
          {d.recovery_at && <button onClick={() => step({ step: 'recovered', amount: Number(amount), note: text }, 'Money back recorded.')}>Money came back</button>}
          <button className="primary" disabled={!d.credit_amount} title={d.credit_amount ? '' : 'Credit the customer first, or close as the customer’s liability'}
            onClick={() => step({ step: 'close', note: text }, 'Dispute resolved.')}>Resolve on the figures</button>
          <button disabled={text.trim().length < 10} onClick={() => step({ step: 'close', outcome: 'customer_liable', note: text }, 'Closed as the customer’s liability.')}>Customer liable</button>
          <button disabled={text.trim().length < 10} onClick={() => step({ step: 'close', outcome: 'withdrawn', note: text }, 'Dispute withdrawn.')}>Withdrawn</button>
        </div>
      </div>}
      <details><summary className="small">{d.history.length} steps on record</summary>
        <ol className="custody">{d.history.map((x, i) => <li key={i}><b>{words(x.what)}</b>{x.amount != null ? ` ${fmtMoney(x.amount)}` : ''} by {nameOf(x.by)}, {fmtDateTime(x.at)}{x.note ? `: ${x.note}` : ''}</li>)}</ol></details>
      {err && <p className="msg" role="alert">{err}</p>}
    </div>
  )
}

/** Disputes on a fraud claim: the customer's money back, the recovery from the other side, and the loss. */
export default function DisputesCard({ caseId, tick, onChange }: { caseId: string; tick: number; onChange: (m: string) => void }) {
  const { data, reload } = useApi<View>(`/cases/${caseId}/disputes`, [tick])
  const [picked, setPicked] = useState<string[] | null>(null)
  const [reason, setReason] = useState('')
  const [err, setErr] = useState<string | null>(null)
  if (!data) return null
  const done = (m: string) => { reload(); onChange(m) }
  const openIt = () => picked && api.post(`/cases/${caseId}/disputes`, { txn_ids: picked, reason: reason || data.reasons[0]?.code })
    .then(() => { setPicked(null); setErr(null); done('Dispute opened. Its clocks have started.') }).catch(e => setErr(e.message))
  const free = data.payments.filter(p => !p.disputed)
  return (
    <Card title={`Disputes (${data.disputes.length})`} action={data.can_work && data.can_open && !picked && free.length > 0 ? <button onClick={() => setPicked([])}>Open a dispute…</button> : undefined}>
      {picked && <div className="override-form">
        <div className="scroll tall short"><table><thead><tr><th /><th>When</th><th>Paid to</th><th>How</th><th className="num">Amount</th></tr></thead>
          <tbody>{free.map(p => <tr key={p.id}>
            <td><input type="checkbox" aria-label={`Dispute ${p.id}`} checked={picked.includes(p.id)} onChange={e => setPicked(e.target.checked ? [...picked, p.id] : picked.filter(x => x !== p.id))} /></td>
            <td className="nowrap">{fmtDateTime(p.ts)}</td><td>{p.counterparty || '–'}</td><td>{p.channel}</td><td className="num">{fmtMoney(p.amount)}</td></tr>)}</tbody></table></div>
        <div className="inline"><select aria-label="Reason" value={reason} onChange={e => setReason(e.target.value)}>{data.reasons.map(r => <option key={r.code} value={r.code}>{r.label}</option>)}</select>
          <button className="primary" disabled={!picked.length} onClick={openIt}>Open dispute for {picked.length} payment{picked.length === 1 ? '' : 's'}</button>
          <button onClick={() => { setPicked(null); setErr(null) }}>Cancel</button></div>
        <p className="muted small">One dispute covers one kind of payment, because each kind has its own deadlines.</p>
        {err && <p className="msg" role="alert">{err}</p>}
      </div>}
      {data.disputes.length === 0 && !picked ? <p className="empty">No payment on this claim is in dispute yet.</p> : data.disputes.map(d => <One key={d.id} d={d} canWork={data.can_work} onChange={done} />)}
    </Card>
  )
}
