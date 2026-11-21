import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, daysUntil, fmtDate, fmtMoney } from '../api'
import { useApi } from '../useApi'
import { usePeople } from '../shell/people'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import DataGrid from '../components/grid/DataGrid'
import { SearchInput } from '../components/grid/controls'
import type { GridColumn, SortState } from '../components/grid/types'

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
const STATUS: Record<string, [string, string]> = {
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
  const [q, setQ] = useState('')
  const [sorting, setSorting] = useState<SortState>([{ id: 'prepared', desc: true }])
  const columns = useMemo<GridColumn<FilingRow>[]>(() => [
    { id: 'kind', accessorFn: f => f.kind, header: 'Report', meta: { label: 'Report', pinned: true }, enableHiding: false,
      cell: ({ row: { original: f } }) => <><b>{f.kind}</b>{f.amends_id && <Pill>Amendment</Pill>}<div className="muted small">{f.id}</div></> },
    { id: 'subject', accessorFn: f => f.subject, header: 'Subject', meta: { label: 'Subject' }, cell: ({ row: { original: f } }) => <>{f.subject}<div className="muted small">{f.party_id}</div></> },
    { id: 'amount', accessorFn: f => f.amount ?? -1, header: 'Amount', meta: { label: 'Amount', align: 'right' }, cell: ({ row: { original: f } }) => (f.amount != null ? fmtMoney(f.amount) : '–') },
    { id: 'activity', accessorFn: f => f.from ?? '', header: 'Activity', meta: { label: 'Activity', className: 'nowrap' },
      cell: ({ row: { original: f } }) => (f.from ? `${fmtDate(`${f.from}T12:00:00`)}${f.to && f.to !== f.from ? ` to ${fmtDate(`${f.to}T12:00:00`)}` : ''}` : '–') },
    { id: 'status', accessorFn: f => f.status, header: 'Status', meta: { label: 'Status' },
      cell: ({ row: { original: f } }) => <><Status f={f} />{f.status === 'prepared' && f.problems.length > 0 && <div className="muted small">{f.problems.length} problem{f.problems.length === 1 ? '' : 's'}</div>}</> },
    { id: 'reference', accessorFn: f => f.reference, header: "Regulator's reference", meta: { label: "Regulator's reference" }, cell: ({ row }) => row.original.reference || <span className="muted">–</span> },
    { id: 'prepared', accessorFn: f => f.prepared_at, header: 'Prepared', meta: { label: 'Prepared' }, cell: ({ row }) => fmtDate(row.original.prepared_at) },
    { id: 'next', accessorFn: f => f.due ?? f.continuing_review_due ?? '', header: 'Next date', meta: { label: 'Next date' },
      cell: ({ row: { original: f } }) => (f.status === 'prepared' && f.due ? `Due ${fmtDate(f.due)}` : f.continuing_review_due ? `Review by ${fmtDate(f.continuing_review_due)}` : <span className="muted">–</span>) },
    { id: 'channel', accessorFn: f => f.channel, header: 'Sent by', meta: { label: 'Sent by', hidden: true }, cell: ({ row }) => row.original.channel || '–' },
  ], [])
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
        <DataGrid<FilingRow> id="filings" label="Regulatory reports" columns={columns} rows={rows.filter(f => !q || `${f.kind} ${f.subject} ${f.party_id} ${f.id} ${f.reference}`.toLowerCase().includes(q.toLowerCase()))}
          getRowId={f => f.id} sorting={sorting} onSortingChange={setSorting} onOpen={f => nav(`/filings/${f.id}`)} empty="No reports here."
          toolbar={<SearchInput value={q} onChange={setQ} placeholder="Subject, report or reference" />} />
        <p className="muted small">An accepted suspicious-activity report is looked at again within 120 days in case the activity has carried on.</p>
      </Card>
    </>
  )
}
