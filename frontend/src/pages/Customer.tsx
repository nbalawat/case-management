import { Link, useNavigate, useParams } from 'react-router-dom'
import { fmtDate, fmtMoney } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Loading, Pill, Priority } from '../components/ui'
import NetworkGraph, { FLAG_LABEL, type Network } from '../components/NetworkGraph'

type Counterparty = { entity_id: string; name: string; kind: string; party_id: string | null; flags: string[]; countries: string[]; txns: number; sent: number; received: number; last: string; spellings: number }
type CaseRef = { id: string; type_title: string; state_label: string; open: boolean; priority: string; created: string; disposition: string | null; restricted: boolean; queue_label: string }
type Related = { party_id: string; name: string; risk_rating: string; open_cases: number; direct: boolean; shared: string[]; flagged: boolean }
type Profile = {
  party: { id: string; name: string; kind: string; occupation: string; country: string; risk_rating: string; pep: boolean; onboarded: string; accounts: { id: string; product: string; status: string }[] }
  totals: { txns: number; in: number; out: number; first: string | null; last: string | null; counterparties: number; alerts: number; open_cases: number; cases: number }
  activity: { month: string; in: number; out: number; txns: number }[]
  channels: { channel: string; txns: number; total: number }[]
  counterparties: Counterparty[]; more_counterparties: number; flagged: Counterparty[]; cases: CaseRef[]; related: Related[]
  duplicates: { party_id: string; name: string; status: string; score: number; reasons: string[] }[]
  media: { id: string; source: string; title: string; published: string }[]
  network: Network | null
}
const RISK: Record<string, string> = { high: 'critical', medium: 'warning', low: 'good' }
export const Flags = ({ flags }: { flags: string[] }) => <>{flags.map(f => <Pill key={f} tone={f === 'common' ? 'neutral' : 'serious'}>{FLAG_LABEL[f] ?? f}</Pill>)}</>

/** Money in and out by month: two series on one scale, with the figures in the table beside it. */
function Activity({ rows }: { rows: Profile['activity'] }) {
  const top = Math.max(1, ...rows.flatMap(r => [r.in, r.out]))
  return (
    <div className="months">
      <div className="net-key"><i className="k in" />Money in<i className="k out" />Money out</div>
      {rows.map(r => (
        <div key={r.month} className="month" title={`${r.txns} payments`}>
          <span>{new Date(`${r.month}-15`).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })}</span>
          <div><i className="in" style={{ width: `${(100 * r.in) / top}%` }} /><i className="out" style={{ width: `${(100 * r.out) / top}%` }} /></div>
          <span className="num">{fmtMoney(r.in)}<br />{fmtMoney(r.out)}</span>
        </div>))}
    </div>
  )
}

export default function Customer() {
  const { id } = useParams()
  const nav = useNavigate()
  const { data, error } = useApi<Profile>(`/customers/${id}`)
  if (!data) return <Loading error={error} />
  const { party: p, totals: t } = data
  const open = (c: Counterparty) => nav(c.party_id ? `/customers/${c.party_id}` : `/counterparties/${c.entity_id}`)
  return (
    <>
      <div className="page-h">
        <div className="crumbs"><Link to="/entities">Entities</Link> / Customer</div>
        <h1>{p.name}</h1>
        <p>{p.id} · {p.kind === 'person' ? p.occupation || 'Individual' : 'Business'} · {p.country} · customer since {fmtDate(p.onboarded)} ·{' '}
          <Pill tone={RISK[p.risk_rating]}>{p.risk_rating} risk</Pill>{p.pep && <Pill tone="serious">Politically exposed</Pill>}
          {p.accounts.map(a => <Pill key={a.id}>{a.product} {a.id}{a.status !== 'open' ? ` (${a.status})` : ''}</Pill>)}</p>
      </div>
      {data.duplicates.length > 0 && <div className="notice">This may not be the only record for this person: {data.duplicates.map((d, i) => (
        <span key={d.party_id}>{i > 0 && ', '}<Link to={`/customers/${d.party_id}`}>{d.name} ({d.party_id})</Link> {d.status === 'same' ? 'was confirmed as the same person' : `is a possible duplicate (${d.reasons.join(', ').toLowerCase()})`}</span>))}.
        {' '}<Link to="/entities">Open the resolution desk</Link></div>}
      <div className="kpis">
        <Kpi label="Money in, last 90 days" value={fmtMoney(t.in)} />
        <Kpi label="Money out, last 90 days" value={fmtMoney(t.out)} sub={`${t.txns} payments`} />
        <Kpi label="Counterparties" value={t.counterparties} sub={data.flagged.length ? `${data.flagged.length} flagged` : 'none flagged'} bad={data.flagged.length > 0} />
        <Kpi label="Alerts" value={t.alerts} />
        <Kpi label="Cases" value={t.cases} sub={`${t.open_cases} open`} bad={t.open_cases > 0} />
      </div>
      {data.network && <Card title="Payment network"><NetworkGraph network={data.network} /></Card>}
      <div className="cols-2">
        <Card title="Connected customers">
          {data.related.length === 0 ? <p className="empty">No other customer shares an uncommon counterparty with this one.</p> : (
            <table className="rows"><thead><tr><th>Customer</th><th>Risk</th><th>Connection</th><th className="num">Open cases</th></tr></thead>
              <tbody>{data.related.map(r => (
                <tr key={r.party_id} tabIndex={0} onClick={() => nav(`/customers/${r.party_id}`)} onKeyDown={e => e.key === 'Enter' && nav(`/customers/${r.party_id}`)}>
                  <td><b>{r.name}</b><div className="muted small">{r.party_id}</div></td>
                  <td><Pill tone={RISK[r.risk_rating]}>{r.risk_rating}</Pill></td>
                  <td>{r.direct && <Pill tone="human">Pays or is paid directly</Pill>}{r.shared.length > 0 && <>Both deal with {r.shared.slice(0, 3).join(', ')}{r.shared.length > 3 ? ` and ${r.shared.length - 3} more` : ''}</>}{r.flagged && <Pill tone="serious">flagged</Pill>}</td>
                  <td className="num">{r.open_cases || <span className="muted">–</span>}</td>
                </tr>))}</tbody></table>)}
        </Card>
        <Card title="Activity by month">
          {data.activity.length === 0 ? <p className="empty">No payments on record.</p> : <Activity rows={data.activity} />}
          <p className="muted small">{data.channels.map(c => `${c.channel}: ${c.txns} payments, ${fmtMoney(c.total)}`).join(' · ')}</p>
        </Card>
      </div>
      <Card title="Cases">
        {data.cases.length === 0 ? <p className="empty">No cases that you can see involve this customer.</p> : (
          <table className="rows"><thead><tr><th>Priority</th><th>Case</th><th>Stage</th><th>Queue</th><th>Opened</th></tr></thead>
            <tbody>{data.cases.map(c => (
              <tr key={c.id} tabIndex={0} onClick={() => nav(`/cases/${c.id}`)} onKeyDown={e => e.key === 'Enter' && nav(`/cases/${c.id}`)}>
                <td><Priority p={c.priority} /></td><td><b>{c.type_title}</b>{c.restricted && <Pill tone="critical">Restricted</Pill>}<div className="muted small">{c.id}</div></td>
                <td>{c.open ? c.state_label : <span className="muted">Closed{c.disposition ? `: ${c.disposition.replaceAll('_', ' ')}` : ''}</span>}</td><td>{c.queue_label}</td><td>{fmtDate(c.created)}</td>
              </tr>))}</tbody></table>)}
      </Card>
      <Card title="Counterparties" action={<span className="muted small">Spellings of one firm are counted together</span>}>
        <div className="scroll"><table className="rows">
          <thead><tr><th>Counterparty</th><th>Countries</th><th className="num">Payments</th><th className="num">Sent to them</th><th className="num">Received from them</th><th>Last</th></tr></thead>
          <tbody>{data.counterparties.map(c => (
            <tr key={c.entity_id} tabIndex={0} onClick={() => open(c)} onKeyDown={e => e.key === 'Enter' && open(c)}>
              <td><b>{c.name}</b> {c.party_id && <Pill tone="human">Also a customer</Pill>}<Flags flags={c.flags} />{c.spellings > 1 && <span className="muted small"> {c.spellings} spellings</span>}</td>
              <td>{c.countries.join(', ')}</td><td className="num">{c.txns}</td><td className="num">{c.sent ? fmtMoney(c.sent) : '–'}</td><td className="num">{c.received ? fmtMoney(c.received) : '–'}</td><td>{fmtDate(c.last)}</td>
            </tr>))}</tbody>
        </table></div>
        {data.more_counterparties > 0 && <p className="muted small">And {data.more_counterparties} smaller counterparties.</p>}
      </Card>
      {data.media.length > 0 && <Card title="News and public records">
        <table><tbody>{data.media.map(m => <tr key={m.id}><td><b>{m.title}</b><div className="muted small">{m.source} · {fmtDate(m.published)}</div></td></tr>)}</tbody></table>
      </Card>}
    </>
  )
}
