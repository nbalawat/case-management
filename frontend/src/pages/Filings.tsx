import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, daysUntil, fmtDate, fmtMoney } from '../api'
import { useApi } from '../useApi'
import { usePeople } from '../App'
import { Card, Kpi, Loading, Pill } from '../components/ui'

export type FilingRow = {
  id: string; kind: string; status: string; format_title: string; case_id: string | null; party_id: string; subject: string; amount: number | null
  from: string | null; to: string | null; prepared_by: string; prepared_at: string; due: string | null; submitted_at: string | null; channel: string
  receipt: string; reference: string; answered_at: string | null; rejection: string; amends_id: string; problems: string[]; sha256: string
  continuing_review_due: string | null
}
type CashDay = { party_id: string; name: string; day: string; direction: string; total: number; count: number }
type Register = {
  can_submit: boolean
  profile: { regime: string; institution: Record<string, string>; missing: string[]; channel_title: string; formats: { id: string; title: string }[] }
  counts: Record<string, number>; filings: FilingRow[]; cash_days: CashDay[]
}
export const STATUS: Record<string, [string, string]> = {
  prepared: ['Not sent', 'warning'], submitted: ['With the regulator', 'human'], acknowledged: ['Accepted', 'good'],
  rejected: ['Rejected', 'critical'], superseded: ['Replaced', 'neutral'],
}
export const Status = ({ f }: { f: { status: string; problems: string[] } }) => {
  const [label, tone] = STATUS[f.status] ?? [f.status, 'neutral']
  return <Pill tone={f.status === 'prepared' && f.problems.length ? 'critical' : tone}>{f.status === 'prepared' && f.problems.length ? 'Needs fixing' : label}</Pill>
}
const REGIME: Record<string, string> = { fincen: 'FinCEN (United States)', goaml: 'goAML (financial intelligence unit)' }

export default function Filings() {
  const { data, error, reload } = useApi<Register>('/filings')
  const { holder } = usePeople()
  const nav = useNavigate()
  const [show, setShow] = useState('open')
  const [busy, setBusy] = useState<string | null>(null)
  const [fail, setFail] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const prepare = (d: CashDay) => {
    setBusy(d.party_id + d.day); setFail(null)
    api.post<FilingRow>('/filings/ctr', { party_id: d.party_id, day: d.day }).then(f => nav(`/filings/${f.id}`)).catch(e => setFail(e.message)).finally(() => { setBusy(null); reload() })
  }
  const c = data.counts
  const rows = data.filings.filter(f => show === 'all' || (show === 'open' ? ['prepared', 'submitted', 'rejected'].includes(f.status) : f.status === show))
  const inst = data.profile.institution
  return (
    <>
      <div className="page-h"><h1>Regulatory reports</h1>
        <p>{inst.name || 'This client'} reports to {REGIME[data.profile.regime] ?? data.profile.regime} as {data.profile.formats.map(f => f.title).join(' and ')}. Reports leave by: {data.profile.channel_title.toLowerCase()}.</p></div>
      {data.profile.missing.length > 0 && <div className="notice">Nothing can be sent yet. The filing institution's details are incomplete: {data.profile.missing.join(', ')}. Platform staff set these for the client.</div>}
      <div className="kpis">
        <Kpi label="Not sent" value={c.prepared} bad={c.prepared > 0} sub="prepared, waiting for a person" />
        <Kpi label="With the regulator" value={c.submitted} sub="sent, no answer yet" />
        <Kpi label="Rejected" value={c.rejected} bad={c.rejected > 0} sub="to correct and send again" />
        <Kpi label="Accepted" value={c.acknowledged} sub="with the regulator's reference" />
        <Kpi label="Cash days owing a CTR" value={data.cash_days.length} bad={data.cash_days.length > 0} />
      </div>
      {fail && <p className="notice">{fail}</p>}
      {data.cash_days.length > 0 && <Card title="Cash over $10,000 in one day, with no report yet">
        {!data.can_submit && <p className="muted">You can read this but not prepare reports. {holder('submit_filings')} can.</p>}
        <table><thead><tr><th>Customer</th><th>Day</th><th>Direction</th><th className="num">Cash</th><th className="num">Movements</th><th>Report due</th>{data.can_submit && <th />}</tr></thead>
          <tbody>{data.cash_days.map(d => {
            const due = new Date(new Date(d.day).getTime() + 15 * 86400000).toISOString()
            return (
              <tr key={d.party_id + d.day + d.direction}>
                <td><b>{d.name}</b><div className="muted small">{d.party_id}</div></td><td>{fmtDate(`${d.day}T12:00:00`)}</td><td>{d.direction === 'in' ? 'Paid in' : 'Taken out'}</td>
                <td className="num">{fmtMoney(d.total)}</td><td className="num">{d.count}</td>
                <td>{(daysUntil(due) ?? 0) < 0 ? <Pill tone="critical">{-(daysUntil(due) ?? 0)}d overdue</Pill> : `${daysUntil(due)}d left`}</td>
                {data.can_submit && <td><button disabled={busy === d.party_id + d.day} onClick={() => prepare(d)}>Prepare CTR</button></td>}
              </tr>)
          })}</tbody></table>
      </Card>}
      <Card title="Reports" action={
        <div className="seg" role="group" aria-label="Status">
          {[['open', 'Needs attention'], ['acknowledged', 'Accepted'], ['superseded', 'Replaced'], ['all', 'All']].map(([k, label]) =>
            <button key={k} aria-pressed={show === k} onClick={() => setShow(k)}>{label}</button>)}
        </div>}>
        {rows.length === 0 ? <p className="empty">No reports here.</p> : (
          <div className="scroll"><table className="rows">
            <thead><tr><th>Report</th><th>Subject</th><th className="num">Amount</th><th>Activity</th><th>Status</th><th>Regulator's reference</th><th>Prepared</th><th>Next date</th></tr></thead>
            <tbody>{rows.map(f => (
              <tr key={f.id} tabIndex={0} onClick={() => nav(`/filings/${f.id}`)} onKeyDown={e => e.key === 'Enter' && nav(`/filings/${f.id}`)}>
                <td><b>{f.kind}</b>{f.amends_id && <Pill>Amendment</Pill>}<div className="muted small">{f.id}</div></td>
                <td>{f.subject}<div className="muted small">{f.party_id}</div></td>
                <td className="num">{f.amount != null ? fmtMoney(f.amount) : '–'}</td>
                <td className="nowrap">{f.from ? `${fmtDate(`${f.from}T12:00:00`)}${f.to && f.to !== f.from ? ` to ${fmtDate(`${f.to}T12:00:00`)}` : ''}` : '–'}</td>
                <td><Status f={f} />{f.status === 'prepared' && f.problems.length > 0 && <div className="muted small">{f.problems.length} problem{f.problems.length === 1 ? '' : 's'}</div>}</td>
                <td>{f.reference || <span className="muted">–</span>}</td><td>{fmtDate(f.prepared_at)}</td>
                <td>{f.status === 'prepared' && f.due ? `Due ${fmtDate(f.due)}` : f.continuing_review_due ? `Review by ${fmtDate(f.continuing_review_due)}` : <span className="muted">–</span>}</td>
              </tr>))}</tbody>
          </table></div>)}
        <p className="muted small">An accepted suspicious-activity report is looked at again within 120 days in case the activity has carried on.</p>
      </Card>
    </>
  )
}
