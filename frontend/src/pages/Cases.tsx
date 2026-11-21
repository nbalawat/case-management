import { useCallback, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Download, Eye, X } from 'lucide-react'
import { api, daysUntil, fmtDate, hoursUntil } from '../api'
import { useApi } from '../useApi'
import { useNameOf } from '../shell/people'
import BulkBar from '../components/BulkBar'
import CaseQuickView from '../components/case/CaseQuickView'
import DataGrid from '../components/grid/DataGrid'
import { FacetFilter, Pager, SearchInput, Segmented, ViewsMenu } from '../components/grid/controls'
import { load, save, type SavedView } from '../components/grid/prefs'
import type { GridColumn } from '../components/grid/types'
import { toSortParam, useListParams } from '../components/grid/useListParams'
import { drillChips } from '../components/dashboard/drill'
import { Button } from '../components/kit/button'
import { Card, Pill, Priority, Rec, Sla, StageClock } from '../components/ui'

export type CaseRow = {
  id: string; case_type: string; type_title: string; state: string; state_label: string; open: boolean; priority: string
  owner: string | null; party_id: string; party_name: string; restricted?: boolean; created: string; sla_due: string | null; sla_clock: string | null
  disposition: string | null; agent_recommendation: string | null; alerts: number
  domain: string; state_kind: string; queue: string | null; queue_label: string; stage_due: string | null; stage_breached: boolean; parent_id: string | null
}
type Page = {
  rows: CaseRow[]; total: number; page: number; size: number
  facets: Record<'case_type' | 'priority' | 'queue' | 'state' | 'owner', Record<string, number>>
  labels: { case_type: Record<string, string>; queue: Record<string, string>; state: Record<string, string>; scenario?: Record<string, string> }
}

const GRID = 'cases'
const DEFAULTS = { status: 'open', page: '1', size: '50', sort: '' }
const PRIORITIES = ['critical', 'high', 'medium', 'low']
const BUILT_IN: SavedView[] = [
  { name: 'Open, most urgent first', query: '' },
  { name: 'Assigned to me', query: 'owner=me' },
  { name: 'Unassigned', query: 'owner=unassigned' },
  { name: 'Critical and high', query: 'priority=critical&priority=high' },
  { name: 'Newest first', query: 'status=all&sort=-created' },
  { name: 'Recently closed', query: 'status=closed&sort=-created' },
]

/** Facet options in a stable order, with counts; chosen values stay listed even when their count is zero. */
const options = (counts: Record<string, number> = {}, chosen: string[], label: (v: string) => string, order?: string[]) => {
  const keys = [...new Set([...(order ?? Object.keys(counts).sort((a, b) => label(a).localeCompare(label(b)))), ...chosen])]
  return keys.filter(k => k in counts || chosen.includes(k)).map(k => ({ value: k, label: label(k), count: counts[k] ?? 0 }))
}

export default function Cases() {
  const p = useListParams(DEFAULTS)
  const { data, error, reload } = useApi<Page>(`/case-list?${p.apiQuery}`)
  const intake = useApi<{ counts: Record<string, number> }>('/alerts/intake').data
  const nameOf = useNameOf()
  const nav = useNavigate()
  const [picked, setPicked] = useState<string[]>([])
  const [preview, setPreview] = useState<string | null>(null)
  const [views, setViews] = useState<SavedView[]>(() => load(GRID, 'views', []))
  const rows = useMemo(() => data?.rows ?? [], [data])
  const page = Number(p.get('page')), size = Number(p.get('size'))
  const labels = data?.labels
  const owner = p.get('owner')

  const step = useCallback((delta: number) => setPreview(cur => {
    const i = rows.findIndex(r => r.id === cur)
    return rows[Math.min(rows.length - 1, Math.max(0, i + delta))]?.id ?? cur
  }), [rows])

  const columns = useMemo<GridColumn<CaseRow>[]>(() => [
    { id: 'priority', accessorFn: c => c.priority, header: 'Priority', meta: { label: 'Priority' }, cell: ({ row }) => <Priority p={row.original.priority} /> },
    { id: 'customer', accessorFn: c => c.party_name, header: 'Customer', meta: { label: 'Customer', pinned: true }, enableHiding: false, cell: ({ row: { original: c } }) => <>
      <b>{c.party_name}</b>{c.restricted && <Pill tone="critical">Restricted</Pill>}
      <div className="muted small"><Link to={`/cases/${c.id}`} onClick={e => e.stopPropagation()}>{c.id}</Link></div></> },
    { id: 'type', accessorFn: c => c.type_title, header: 'Case type', meta: { label: 'Case type' }, cell: ({ row }) => row.original.type_title },
    { id: 'stage', accessorFn: c => c.state_label, header: 'Stage', meta: { label: 'Stage' }, cell: ({ row: { original: c } }) => <>{c.state_label}{c.state_kind === 'wait' && <Pill tone="warning">waiting</Pill>}</> },
    { id: 'clock', header: 'Stage clock', enableSorting: false, meta: { label: 'Stage clock' },
      cell: ({ row: { original: c } }) => (c.open ? <StageClock hours={hoursUntil(c.stage_due)} /> : <span className="muted">–</span>) },
    { id: 'alerts', header: 'Alerts', enableSorting: false, meta: { label: 'Alerts', align: 'right' }, cell: ({ row }) => row.original.alerts },
    { id: 'agent', header: 'Agent view', enableSorting: false, meta: { label: 'Agent view' }, cell: ({ row }) => <Rec r={row.original.agent_recommendation} /> },
    { id: 'deadline', accessorFn: c => c.sla_due, header: 'Deadline', meta: { label: 'Deadline' }, cell: ({ row: { original: c } }) => <Sla days={daysUntil(c.sla_due)} open={c.open} /> },
    { id: 'created', accessorFn: c => c.created, header: 'Opened', meta: { label: 'Opened', className: 'nowrap' }, cell: ({ row }) => fmtDate(row.original.created) },
    { id: 'owner', accessorFn: c => c.owner, header: 'Queue and owner', meta: { label: 'Queue and owner' }, cell: ({ row: { original: c } }) => (c.open
      ? <>{c.owner ? nameOf(c.owner) : <span className="muted">Unassigned</span>}<div className="muted small">{c.queue_label}</div></>
      : <span className="muted">{c.disposition === 'merged' ? 'Merged' : 'Closed'}</span>) },
    { id: 'domain', header: 'Area', enableSorting: false, meta: { label: 'Area', hidden: true }, cell: ({ row }) => row.original.domain },
    { id: 'preview', header: '', enableSorting: false, enableHiding: false, meta: { pinned: true, className: 'w-10' },
      cell: ({ row }) => <Button size="icon" variant="ghost" className="h-8 w-8" aria-label={`Quick view of ${row.original.party_name}`} title="Quick view (Space)"
        onClick={e => { e.stopPropagation(); setPreview(row.original.id) }}><Eye size={16} /></Button> },
  ], [nameOf])

  const f = data?.facets
  const ownerOptions = [['', 'Anyone'], ['me', 'Me'], ['unassigned', 'Unassigned']] as [string, string][]
  return (
    <>
      <div className="page-h"><h1>Cases</h1><p>Every case you can see across monitoring, sanctions, fraud, KYC and QA. Filter, sort and save views; press Space on a row for a quick look.
        {intake && (intake.counts.hibernated ?? 0) > 0 && <> <Link to="/alerts">{intake.counts.hibernated} weak alert{intake.counts.hibernated > 1 ? 's are' : ' is'} waiting</Link> instead of opening a case.</>}</p></div>
      <Card>
        {picked.length > 0 && <BulkBar ids={picked} onDone={() => { setPicked([]); reload() }} onClear={() => setPicked([])} />}
        {error && !data && <p className="msg">Could not load: {error}</p>}
        <DataGrid<CaseRow>
          id={GRID} label="Cases" columns={columns} rows={rows} getRowId={r => r.id} manualSorting loading={!data}
          sorting={p.sort} onSortingChange={s => p.set({ sort: toSortParam(s) })}
          selectable={r => r.open} selected={picked} onSelectedChange={setPicked}
          onOpen={r => nav(`/cases/${r.id}`)} onPreview={r => setPreview(r.id)}
          rowClassName={r => (r.open && r.stage_breached ? 'breached' : '')}
          empty="No cases match these filters."
          toolbar={<>
            <ViewsMenu builtIn={BUILT_IN} saved={views} current={p.query.replace(/(^|&)(page|size)=[^&]*/g, '').replace(/^&/, '')}
              onApply={v => p.replaceAll(v.query)}
              onSave={name => { const next = [...views.filter(v => v.name !== name), { name, query: p.query }]; setViews(next); save(GRID, 'views', next) }}
              onDelete={name => { const next = views.filter(v => v.name !== name); setViews(next); save(GRID, 'views', next) }} />
            <Segmented label="Status" value={p.get('status')} onChange={v => p.set({ status: v })} options={[['open', 'Open'], ['closed', 'Closed'], ['all', 'All']]} />
            <SearchInput value={p.get('q')} onChange={v => p.set({ q: v })} placeholder="Customer, customer ID or case ID" />
            <FacetFilter label="Case type" value={p.getAll('case_type')} onChange={v => p.set({ case_type: v })}
              options={options(f?.case_type, p.getAll('case_type'), v => labels?.case_type[v] ?? v)} />
            <FacetFilter label="Priority" value={p.getAll('priority')} onChange={v => p.set({ priority: v })}
              options={options(f?.priority, p.getAll('priority'), v => v[0].toUpperCase() + v.slice(1), PRIORITIES)} />
            <FacetFilter label="Stage" value={p.getAll('state')} onChange={v => p.set({ state: v })}
              options={options(f?.state, p.getAll('state'), v => labels?.state[v] ?? v)} />
            <FacetFilter label="Queue" value={p.getAll('queue')} onChange={v => p.set({ queue: v })}
              options={options(f?.queue, p.getAll('queue'), v => labels?.queue[v] ?? v)} />
            <Segmented label="Owner" value={owner} onChange={v => p.set({ owner: v })} options={ownerOptions} />
            {drillChips(new URLSearchParams(p.query), s => labels?.scenario?.[s] ?? s).map(c => (
              <button key={c.key + c.label} className="drill-chip" title="Set by a dashboard. Remove this filter" onClick={() => p.set({ [c.key]: null })}>
                {c.label} <X size={13} aria-hidden /></button>))}
          </>}
          footer={<>
            <Button size="sm" onClick={() => api.download(`/case-list/export.csv?${p.apiQuery}`)}><Download size={15} /> Export {data ? data.total.toLocaleString() : ''} to CSV</Button>
            {data && <Pager page={page} size={size} total={data.total} onPage={n => p.set({ page: n }, false)} onSize={n => p.set({ size: n })} />}
          </>}
        />
      </Card>
      <CaseQuickView id={preview} onClose={() => setPreview(null)} onStep={step} />
    </>
  )
}
