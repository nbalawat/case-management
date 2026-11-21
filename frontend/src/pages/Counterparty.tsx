import { Link, useNavigate, useParams } from 'react-router-dom'
import { fmtDate, fmtMoney } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import { Flags } from './Customer'

type Detail = {
  entity: { id: string; name: string; party_id: string | null; countries: string[]; flags: string[]; customers: number; txns: number; total: number
    aliases: { name: string; count: number; how: string; score: number }[] }
  customers: { party_id: string; name: string; risk_rating: string; txns: number; sent: number; received: number; last: string; open_cases: number }[]
  more_customers: number
}
const RISK: Record<string, string> = { high: 'critical', medium: 'warning', low: 'good' }

export default function Counterparty() {
  const { id } = useParams()
  const nav = useNavigate()
  const { data, error } = useApi<Detail>(`/counterparties/${id}`)
  if (!data) return <Loading error={error} />
  const e = data.entity
  return (
    <>
      <div className="page-h">
        <div className="crumbs"><Link to="/entities">Entities</Link> / Counterparty</div>
        <h1>{e.name}</h1>
        <p>An outside party your customers pay or are paid by, known here only by name. <Flags flags={e.flags} />{e.countries.length > 0 && <>Seen in {e.countries.join(', ')}.</>}</p>
      </div>
      <div className="kpis">
        <Kpi label="Customers dealing with it" value={e.customers} />
        <Kpi label="Payments" value={e.txns} />
        <Kpi label="Total value" value={fmtMoney(e.total)} />
        <Kpi label="Spellings treated as this firm" value={e.aliases.length} />
      </div>
      <div className="cols-2">
        <Card title="Customers">
          <table className="rows"><thead><tr><th>Customer</th><th>Risk</th><th className="num">Payments</th><th className="num">Sent</th><th className="num">Received</th><th>Last</th><th className="num">Open cases</th></tr></thead>
            <tbody>{data.customers.map(c => (
              <tr key={c.party_id} tabIndex={0} onClick={() => nav(`/customers/${c.party_id}`)} onKeyDown={k => k.key === 'Enter' && nav(`/customers/${c.party_id}`)}>
                <td><b>{c.name}</b><div className="muted small">{c.party_id}</div></td><td><Pill tone={RISK[c.risk_rating]}>{c.risk_rating}</Pill></td>
                <td className="num">{c.txns}</td><td className="num">{c.sent ? fmtMoney(c.sent) : '–'}</td><td className="num">{c.received ? fmtMoney(c.received) : '–'}</td>
                <td>{fmtDate(c.last)}</td><td className="num">{c.open_cases || <span className="muted">–</span>}</td>
              </tr>))}</tbody></table>
          {data.more_customers > 0 && <p className="muted small">And {data.more_customers} more customers.</p>}
        </Card>
        <Card title="Why these spellings are one firm">
          <table><thead><tr><th>As written on payments</th><th className="num">Payments</th><th>Why it was matched</th></tr></thead>
            <tbody>{e.aliases.map(a => <tr key={a.name}><td><b>{a.name}</b></td><td className="num">{a.count}</td>
              <td>{a.how[0].toUpperCase() + a.how.slice(1)}{a.score < 1 && <span className="muted"> ({Math.round(a.score * 100)}% alike)</span>}</td></tr>)}</tbody></table>
        </Card>
      </div>
    </>
  )
}
