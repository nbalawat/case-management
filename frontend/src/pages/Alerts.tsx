import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmtDate, words } from '../api'
import { useApi } from '../useApi'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import DataGrid from '../components/grid/DataGrid'
import { SearchInput, Segmented } from '../components/grid/controls'
import type { GridColumn, SortState } from '../components/grid/types'

type Rule = { code: string; label: string; scenario: string; below: number; days: number }
type Waiting = {
  id: string; status: string; scenario: string; score: number; party_id: string; party_name: string; source: string; explanation: string
  created: string; wake_by: string | null; note: string
}
type Intake = { counts: Record<string, number>; rules: Rule[]; can_wake: boolean; alerts: Waiting[] }

export default function Alerts() {
  const { data, error, reload } = useApi<Intake>('/alerts/intake')
  const [msg, setMsg] = useState<string | null>(null)
  const nav = useNavigate()
  const [show, setShow] = useState('all')
  const [q, setQ] = useState('')
  const [sorting, setSorting] = useState<SortState>([{ id: 'created', desc: true }])
  const columns = useMemo<GridColumn<Waiting>[]>(() => [
    { id: 'customer', accessorFn: a => a.party_name, header: 'Customer', meta: { label: 'Customer', pinned: true }, enableHiding: false,
      cell: ({ row: { original: a } }) => <><Link to={`/customers/${a.party_id}`} onClick={e => e.stopPropagation()}><b>{a.party_name}</b></Link><div className="muted small">{a.id} · {a.source}</div></> },
    { id: 'scenario', accessorFn: a => a.scenario, header: 'Scenario', meta: { label: 'Scenario' },
      cell: ({ row: { original: a } }) => <>{words(a.scenario)}<div className="muted small">{a.explanation}</div></> },
    { id: 'score', accessorFn: a => a.score, header: 'Score', meta: { label: 'Score', align: 'right' }, cell: ({ row }) => row.original.score.toFixed(2) },
    { id: 'created', accessorFn: a => a.created, header: 'Raised', meta: { label: 'Raised', className: 'nowrap' }, cell: ({ row }) => fmtDate(row.original.created) },
    { id: 'status', accessorFn: a => a.wake_by ?? '', header: 'Status', meta: { label: 'Status' },
      cell: ({ row: { original: a } }) => (a.status === 'hibernated' ? <Pill tone="warning">waiting until {fmtDate(a.wake_by!)}</Pill> : <Pill>closed after waiting</Pill>) },
    { id: 'why', header: 'Why', enableSorting: false, meta: { label: 'Why', className: 'small' }, cell: ({ row }) => row.original.note },
    ...(data?.can_wake ? [{ id: 'act', header: '', enableSorting: false, enableHiding: false, meta: { pinned: true },
      cell: ({ row }) => <button onClick={e => { e.stopPropagation(); wake(row.original) }} title="Open a case for this alert now">Open a case</button> } as GridColumn<Waiting>] : []),
  ], [data?.can_wake]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!data) return <Loading error={error} />
  const n = (k: string) => data.counts[k] ?? 0
  const wake = (a: Waiting) => api.post<{ case_id: string }>(`/alerts/${a.id}/wake`).then(r => nav(`/cases/${r.case_id}`)).catch(e => { setMsg(e.message); reload() })
  return (
    <>
      <div className="crumbs"><Link to="/cases">Cases</Link> / Alert intake</div>
      <div className="page-h"><h1>Alert intake</h1><p>Where every alert went. A weak alert can wait instead of opening a case, and joins the case if another alert arrives for the same customer.</p></div>
      <div className="kpis">
        <Kpi label="In a case" value={n('in_case')} />
        <Kpi label="Waiting" value={n('hibernated')} sub="held by a rule" />
        <Kpi label="Closed after waiting" value={n('expired')} sub="nothing further arrived" />
        <Kpi label="Closed by an agent" value={n('auto_closed')} sub="inside the client's policy" />
      </div>
      <Card title="Hibernation rules" action={<Link to="/reference">Change the rules</Link>}>
        {data.rules.length === 0 ? <p className="empty">This client has no hibernation rule, so every alert opens or joins a case.</p> : (
          <table>
            <thead><tr><th>Rule</th><th>Applies to</th><th className="num">Holds scores below</th><th className="num">Waits</th></tr></thead>
            <tbody>{data.rules.map(r => (
              <tr key={r.code}><td><b>{r.label}</b></td><td>{r.scenario === '*' ? 'Every scenario' : words(r.scenario)}</td><td className="num">{r.below.toFixed(2)}</td><td className="num">{r.days} days</td></tr>))}</tbody>
          </table>)}
        <p className="muted small">A rule only ever holds an alert when the customer has no open case of that kind. Holding and closing are recorded on the alert with the rule that caused them.</p>
      </Card>
      <Card title={`Waiting and closed alerts (${data.alerts.length})`}>
        {msg && <p className="msg" role="status">{msg}</p>}
        <DataGrid<Waiting> id="alerts-intake" label="Waiting and closed alerts" columns={columns} getRowId={a => a.id}
          rows={data.alerts.filter(a => (show === 'all' || a.status === show) && (!q || `${a.party_name} ${a.party_id} ${a.id} ${a.scenario}`.toLowerCase().includes(q.toLowerCase())))}
          sorting={sorting} onSortingChange={setSorting} empty="No alert is waiting."
          onOpen={a => nav(`/customers/${a.party_id}`)}
          toolbar={<>
            <Segmented label="Status" value={show} onChange={setShow} options={[['all', 'All'], ['hibernated', 'Waiting'], ['expired', 'Closed after waiting']]} />
            <SearchInput value={q} onChange={setQ} placeholder="Customer, alert or scenario" />
          </>} />
      </Card>
    </>
  )
}
