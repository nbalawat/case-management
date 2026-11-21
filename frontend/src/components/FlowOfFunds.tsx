import { useState } from 'react'
import { Link } from 'react-router-dom'
import { fmtDate, fmtMoney, words } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Pill } from './ui'
import { FLAG_LABEL } from './networkFlags'

type Side = { name: string; entity_id: string | null; amount: number; txns: number; flags: string[]; countries: string[]; abroad: boolean; other?: boolean }
type Flow = {
  party: { id: string; name: string }; from: string | null; to: string | null; txns: number; total_in: number; total_out: number; net: number
  sources: Side[]; destinations: Side[]
  measures: { rapid_out_share: number; rapid_days: number; cash_share: number; abroad_share: number; high_risk_share: number }
}
const PERIODS: [number, string][] = [[90, 'Last 90 days'], [365, 'Last year'], [0, 'Everything']]
const WIDTH = 780, LABEL = 215, BAR = 10, GAP = 8, PLOT = 240, PAD = 14
const share = (n: number) => `${Math.round(n * 100)}%`
const marked = (n: Side) => n.flags.length > 0
const flagText = (n: Side) => n.flags.map(f => FLAG_LABEL[f] ?? words(f)).join(', ')

/** Stack one side's bars top to bottom, and the matching slices of the customer's bar without gaps. */
const ROW = 28 // room for a two-line label, however thin the bar
function stack(rows: Side[], scale: number) {
  let y = PAD, mid = 0
  return rows.map(n => {
    const h = Math.max(2, n.amount * scale), slot = Math.max(h, ROW)
    const out = { n, y: y + (slot - h) / 2, h, mid, mh: n.amount * scale }
    y += slot + GAP
    mid += n.amount * scale
    return out
  })
}

function Diagram({ flow }: { flow: Flow }) {
  const [hover, setHover] = useState<string | null>(null)
  const scale = PLOT / Math.max(flow.total_in, flow.total_out, 1)
  const left = stack(flow.sources, scale), right = stack(flow.destinations, scale)
  const tall = (s: typeof left) => (s.length ? s[s.length - 1].y + Math.max(s[s.length - 1].h, ROW) : PAD)
  const height = Math.max(tall(left), tall(right), PAD + PLOT) + PAD
  const cx = WIDTH / 2 - BAR / 2, lx = LABEL, rx = WIDTH - LABEL - BAR
  const top = (total: number) => (height - total * scale) / 2
  const inTop = top(flow.total_in), outTop = top(flow.total_out)
  const ribbon = (x1: number, y1: number, h1: number, x2: number, y2: number, h2: number) => {
    const m = (x1 + x2) / 2
    return `M${x1},${y1} C${m},${y1} ${m},${y2} ${x2},${y2} L${x2},${y2 + h2} C${m},${y2 + h2} ${m},${y1 + h1} ${x1},${y1 + h1} Z`
  }
  const tip = (n: Side, way: string) => `${n.name}: ${fmtMoney(n.amount)} ${way} in ${n.txns} payment${n.txns === 1 ? '' : 's'}${n.countries.length ? ` · ${n.countries.join(', ')}` : ''}${n.flags.length ? ` · ${flagText(n)}` : ''}`
  const side = (rows: typeof left, dir: 'in' | 'out') => rows.map(({ n, y, h, mid, mh }) => {
    const key = `${dir}:${n.name}`
    const dim = hover !== null && hover !== key
    return (
      <g key={key} className={`ff ${dir}${marked(n) ? ' flagged' : ''}${dim ? ' dim' : ''}`} onMouseEnter={() => setHover(key)} onMouseLeave={() => setHover(null)}>
        <title>{tip(n, dir === 'in' ? 'received' : 'sent')}</title>
        {dir === 'in'
          ? <path d={ribbon(lx + BAR, y, h, cx, inTop + mid, Math.max(mh, 1))} />
          : <path d={ribbon(cx + BAR, outTop + mid, Math.max(mh, 1), rx, y, h)} />}
        <rect x={dir === 'in' ? lx : rx} y={y} width={BAR} height={h} rx={2} />
        <text x={dir === 'in' ? lx - 8 : rx + BAR + 8} y={y + h / 2} dy="-2" textAnchor={dir === 'in' ? 'end' : 'start'} className="nm">{n.name.length > 30 ? `${n.name.slice(0, 29)}…` : n.name}</text>
        <text x={dir === 'in' ? lx - 8 : rx + BAR + 8} y={y + h / 2} dy="11" textAnchor={dir === 'in' ? 'end' : 'start'} className="sub">
          {fmtMoney(n.amount)}{n.abroad ? ` · ${n.countries.join(', ')}` : ''}{marked(n) ? ` · ${flagText(n)}` : ''}</text>
      </g>)
  })
  return (
    <div className="scroll"><svg className="flow" viewBox={`0 0 ${WIDTH} ${height}`} width={WIDTH} height={height} role="img"
      aria-label={`${fmtMoney(flow.total_in)} came in from ${flow.sources.length} sources and ${fmtMoney(flow.total_out)} went out to ${flow.destinations.length} destinations`}>
      {side(left, 'in')}{side(right, 'out')}
      {flow.total_in > 0 && <rect className="ff-mid in" x={cx} y={inTop} width={BAR / 2 - 1} height={Math.max(flow.total_in * scale, 1)} />}
      {flow.total_out > 0 && <rect className="ff-mid out" x={cx + BAR / 2 + 1} y={outTop} width={BAR / 2 - 1} height={Math.max(flow.total_out * scale, 1)} />}
    </svg></div>
  )
}

/** Where a customer's money came from and where it went: over a period, or only the payments behind one case. */
export default function FlowOfFunds({ partyId, caseId }: { partyId: string; caseId?: string }) {
  const [days, setDays] = useState(90)
  const { data, error } = useApi<Flow>(`/customers/${partyId}/flow?${caseId ? `case_id=${caseId}` : `days=${days}`}`)
  const m = data?.measures
  return (
    <Card title="Flow of funds" action={caseId ? <span className="muted small">Only the payments behind this case's alerts</span> : (
      <span className="seg" role="group" aria-label="Period">{PERIODS.map(([d, label]) => <button key={d} className={d === days ? 'on' : ''} aria-pressed={d === days} onClick={() => setDays(d)}>{label}</button>)}</span>)}>
      {!data ? <p className="empty">{error ? `Could not load: ${error}` : 'Loading…'}</p> : data.txns === 0 ? <p className="empty">No payments in this period.</p> : <>
        <div className="kpis slim">
          <Kpi label="Money in" value={fmtMoney(data.total_in)} sub={`${data.sources.length} source${data.sources.length === 1 ? '' : 's'}`} />
          <Kpi label="Money out" value={fmtMoney(data.total_out)} sub={`${data.destinations.length} destination${data.destinations.length === 1 ? '' : 's'}`} />
          <Kpi label={`Left again within ${m!.rapid_days} days`} value={share(m!.rapid_out_share)} sub="of money in" bad={m!.rapid_out_share >= 0.7} />
          <Kpi label="Cash" value={share(m!.cash_share)} sub="of all movement" bad={m!.cash_share >= 0.5} />
          <Kpi label="Crossed a border" value={share(m!.abroad_share)} sub={m!.high_risk_share ? `${share(m!.high_risk_share)} to or from high-risk countries` : 'none with high-risk countries'} bad={m!.high_risk_share > 0} />
        </div>
        <div className="net-key"><i className="k in" />Money in<i className="k out" />Money out<i className="k flag" />Flagged counterparty
          <span className="muted">· {data.txns} payments, {fmtDate(data.from!)} to {fmtDate(data.to!)}</span></div>
        <Diagram flow={data} />
        <details><summary className="small">The same figures as a table</summary>
          <table className="factors"><thead><tr><th>Direction</th><th>Counterparty</th><th>Countries</th><th className="num">Payments</th><th className="num">Amount</th></tr></thead>
            <tbody>{([['In', data.sources], ['Out', data.destinations]] as [string, Side[]][]).flatMap(([dir, rows]) => rows.map(n => <tr key={dir + n.name}>
              <td>{dir}</td><td>{n.entity_id ? <Link to={`/counterparties/${n.entity_id}`}>{n.name}</Link> : n.name}{n.flags.map(f => <Pill key={f} tone="serious">{FLAG_LABEL[f] ?? words(f)}</Pill>)}</td>
              <td>{n.countries.join(', ')}</td><td className="num">{n.txns}</td><td className="num">{fmtMoney(n.amount)}</td></tr>))}</tbody></table></details>
      </>}
    </Card>
  )
}
