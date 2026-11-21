import { useState } from 'react'
import { api } from '../../api'
import { useApi } from '../../useApi'
import { Card, Loading } from '../ui'

type Meter = { id: string; label: string; unit: string; billable: boolean }
type Client = { tenant: string; name: string; totals: { [meter: string]: number } }
type Usage = { from: string; to: string; meters: Meter[]; clients: Client[] }

const fmt = (v: number, unit: string) => unit === 'USD' ? v.toLocaleString('en-US', { style: 'currency', currency: 'USD' })
  : unit === 'bytes' ? (v < 1e6 ? `${(v / 1e3).toFixed(0)} kB` : `${(v / 1e6).toFixed(1)} MB`) : v.toLocaleString('en-US')
const iso = (d: Date) => d.toISOString().slice(0, 10)

/** What each client used, counted from the records the platform keeps anyway, with a CSV a billing system imports. */
export default function UsageCard() {
  const [start, setStart] = useState(() => iso(new Date(Date.now() - 29 * 86400000)))
  const [end, setEnd] = useState(() => iso(new Date()))
  const [msg, setMsg] = useState<string | null>(null)
  const { data, error } = useApi<Usage>(`/platform/usage?start=${start}&end=${end}`)
  return (
    <Card title="Usage by client" action={<span className="toolbar">
      <input type="date" aria-label="From" value={start} max={end} onChange={e => setStart(e.target.value)} />
      <input type="date" aria-label="To" value={end} min={start} onChange={e => setEnd(e.target.value)} />
      <button className="ghost" onClick={() => api.download(`/platform/usage.csv?start=${start}&end=${end}`).catch(e => setMsg(e.message))}>CSV for billing</button></span>}>
      {msg && <p className="msg" role="status">{msg}</p>}
      {!data ? <Loading error={error} /> : (
        <div className="scroll"><table>
          <thead><tr><th>Client</th>{data.meters.map(m => <th key={m.id} className="num" title={m.billable ? 'Billable' : 'For capacity planning'}>{m.label}</th>)}</tr></thead>
          <tbody>{data.clients.map(c => (
            <tr key={c.tenant}><td className="nowrap"><b>{c.name}</b><div className="muted small">{c.tenant}</div></td>
              {data.meters.map(m => <td key={m.id} className="num">{fmt(c.totals[m.id] ?? 0, m.unit)}</td>)}</tr>))}</tbody>
        </table></div>)}
      <p className="muted small">Every figure is counted from the client's own records (cases, alerts, documents, questions, model calls, the managed-service team's actions), so there is no separate counter to drift from what the client sees. People are counted once over the period, not once a day.</p>
    </Card>
  )
}
