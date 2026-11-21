import { Link } from 'react-router-dom'
import { fmtMoney } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import PageTabs from '../components/layout/PageTabs'
import { Clock, type Dispute } from '../components/DisputesCard'
import { holdWhat, type Hold } from '../components/HoldsCard'

type Reason = { reason: string; claims: number; claimed: number; recovered: number; loss: number }
type Desk = {
  disputes: Dispute[]; holds: Hold[]; can_work: boolean
  summary: { open: number; open_amount: number; exposure: number; credit_overdue: number; resolve_overdue: number; recovery_open: number; closed: number; loss: number
    recovered: number; recovery_rate: number | null; by_reason: Reason[] }
}
const OUT: Record<string, [string, string]> = {
  recovered: ['good', 'Recovered in full'], partly_recovered: ['warning', 'Partly recovered'], written_off: ['critical', 'Written off'],
  customer_liable: ['neutral', 'Customer liable'], withdrawn: ['neutral', 'Withdrawn'],
}
const STATUS: Record<string, string> = { open: 'Claim open', credited: 'Customer credited', recovery: 'Recovery under way' }

/** Claimed money by cause: how much came back and how much was lost, on one scale, with the figures beside the bars. */
function ByReason({ rows }: { rows: Reason[] }) {
  const top = Math.max(1, ...rows.map(r => r.claimed))
  return (
    <div className="reasons">
      <div className="net-key"><i className="k in" />Recovered<i className="k out" />Lost<i className="k rest" />Still open or not credited</div>
      {rows.map(r => {
        const rest = Math.max(0, r.claimed - r.recovered - r.loss)
        return <div key={r.reason} className="reason-row" title={`${r.claims} claim${r.claims === 1 ? '' : 's'}: ${fmtMoney(r.claimed)} claimed, ${fmtMoney(r.recovered)} recovered, ${fmtMoney(r.loss)} lost`}>
          <span>{r.reason}<small>{r.claims} claim{r.claims === 1 ? '' : 's'}</small></span>
          <div className="rstack" style={{ width: `${(100 * r.claimed) / top}%` }}>
            {r.recovered > 0 && <i className="in" style={{ flex: r.recovered }} />}{r.loss > 0 && <i className="out" style={{ flex: r.loss }} />}{rest > 0 && <i className="rest" style={{ flex: rest }} />}
          </div>
          <span className="num">{fmtMoney(r.claimed)}<small>{fmtMoney(r.loss)} lost</small></span>
        </div>
      })}
    </div>
  )
}

/** The fraud desk: every disputed payment with its deadlines, what is frozen, and where the money went. */
export default function Fraud() {
  const { data, error } = useApi<Desk>('/fraud')
  if (!data) return <Loading error={error} />
  const s = data.summary
  const open = data.disputes.filter(d => d.status !== 'closed')
  const closed = data.disputes.filter(d => d.status === 'closed')
  return (
    <>
      <div className="page-h"><h1>Fraud</h1>
        <p>Every disputed payment runs on three clocks set by how it was paid: money back to the customer, recovery from the other side, and the final answer. What is credited and not recovered is the loss. You see the claims on cases you can see.</p></div>
      <div className="kpis">
        <Kpi label="Open disputes" value={s.open} sub={fmtMoney(s.open_amount)} />
        <Kpi label="Credited, not yet recovered" value={fmtMoney(s.exposure)} sub="at risk on open disputes" />
        <Kpi label="Customer credit overdue" value={s.credit_overdue} bad={s.credit_overdue > 0} sub={s.resolve_overdue ? `${s.resolve_overdue} past the resolution date` : 'none past the resolution date'} />
        <Kpi label="Recovery not yet sent" value={s.recovery_open} sub="with a network deadline running" bad={s.recovery_open > 0} />
        <Kpi label="Losses on closed disputes" value={fmtMoney(s.loss)} sub={s.recovery_rate === null ? 'nothing closed yet' : `${Math.round(s.recovery_rate * 100)}% of credits recovered`} />
      </div>
      <PageTabs label="Fraud desk" tabs={[
        { id: 'open', label: 'Open disputes', count: open.length, alert: s.credit_overdue > 0, content: <>
      <Card title={`Open disputes (${open.length})`}>
        {open.length === 0 ? <p className="empty">None open.</p> : (
          <div className="scroll"><table><thead><tr><th>Dispute</th><th>Stage</th><th>Customer credit</th><th>Recovery</th><th>Resolution</th><th>Case</th></tr></thead>
            <tbody>{open.map(d => <tr key={d.id}>
              <td><b>{fmtMoney(d.amount)}</b> by {d.rail}<div className="muted small">{d.reason}</div></td>
              <td><Pill tone="human">{STATUS[d.status]}</Pill></td>
              <td>{d.credit_amount ? <>{fmtMoney(d.credit_amount)} {d.credit_final ? 'final' : 'provisional'}</> : <Clock due={d.credit_due} overdue={d.credit_overdue} label="Credit" />}</td>
              <td>{d.recovery_at ? <>{d.recovery_ref} · {fmtMoney(d.recovered)} back</> : d.recovery_by_request ? <span className="muted">By request, not yet sent</span> : <Clock due={d.recovery_due} overdue={!d.recovery_open} label={`Send ${d.recovery_name}`} />}</td>
              <td><Clock due={d.resolve_due} overdue={d.resolve_overdue} label="Resolve" /></td>
              <td><Link to={`/cases/${d.case_id}`}>{d.case_id}</Link></td>
            </tr>)}</tbody></table></div>)}
      </Card>
        </> },
        { id: 'frozen', label: 'Frozen accounts', count: data.holds.length, alert: data.holds.some(h => h.overdue), content: <>
        <Card title={`Frozen now (${data.holds.length})`}>
          {data.holds.length === 0 ? <p className="empty">Nothing is on hold for fraud.</p> : (
            <table><thead><tr><th>What</th><th>Why</th><th>Step-up check</th><th>Case</th></tr></thead>
              <tbody>{data.holds.map(h => <tr key={h.id}>
                <td><b>{holdWhat(h)}</b><div className="muted small"><Link to={`/customers/${h.party_id}`}>{h.party_id}</Link></div></td>
                <td>{h.reason}{h.overdue && <Pill tone="critical">Review overdue</Pill>}</td>
                <td>{h.challenge ? <Pill tone={h.challenge.status === 'passed' ? 'good' : h.challenge.status === 'failed' ? 'critical' : 'warning'}>{h.challenge.status === 'sent' ? 'Waiting for the customer' : h.challenge.status === 'passed' ? 'Passed' : 'Failed'}</Pill> : <span className="muted">Not sent</span>}</td>
                <td><Link to={`/cases/${h.case_id}`}>{h.case_id}</Link></td>
              </tr>)}</tbody></table>)}
        </Card>
        </> },
        { id: 'closed', label: 'Closed disputes', count: closed.length, content: <>
      <Card title={`Closed disputes (${closed.length})`}>
        {closed.length === 0 ? <p className="empty">None closed.</p> : (
          <div className="scroll"><table><thead><tr><th>Dispute</th><th>Outcome</th><th className="num">Credited</th><th className="num">Recovered</th><th className="num">Loss</th><th>Case</th></tr></thead>
            <tbody>{closed.map(d => <tr key={d.id}>
              <td><b>{fmtMoney(d.amount)}</b> by {d.rail}<div className="muted small">{d.reason}</div></td>
              <td><Pill tone={OUT[d.outcome!]?.[0]}>{OUT[d.outcome!]?.[1]}</Pill>{d.credit_late && <Pill tone="serious">credited late</Pill>}</td>
              <td className="num">{fmtMoney(d.credit_amount)}</td><td className="num">{fmtMoney(d.recovered)}</td><td className="num">{d.loss ? fmtMoney(d.loss) : '–'}</td>
              <td><Link to={`/cases/${d.case_id}`}>{d.case_id}</Link></td>
            </tr>)}</tbody></table></div>)}
      </Card>
        </> },
        { id: 'losses', label: 'Losses by cause', content: <>
        <Card title="Where the money went, by cause">{s.by_reason.length === 0 ? <p className="empty">No disputes yet.</p> : <ByReason rows={s.by_reason} />}</Card>
        </> },
      ]} />
    </>
  )
}
