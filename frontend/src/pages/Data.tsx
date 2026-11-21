import { Fragment, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDateTime, fmtMoney } from '../api'
import { usePeople } from '../App'
import { useApi } from '../useApi'
import { Card, Kpi, Loading, Pill } from '../components/ui'

type Proposal = { target: string; source: string | null; confidence: number; required: boolean; reason: string; samples: string[]; desc: string }
type Stats = {
  objects_found: number; objects_new: number; objects_loaded: number; objects_quarantined: number; read: number; loaded: number; updated: number
  duplicates: number; rejected: number; source_amount: number; loaded_amount: number; alerts: number; cases: number; reconciled: boolean
}
type Step = { step: string; status: string; detail: string }
type Run = {
  id: string; source_id: string; source: string | null; batch_id: string; trigger: string; started_by: string; started: string; finished: string | null
  status: string; mapping_version: number; steps: Step[]; stats: Partial<Stats>; error: string
}
type Obj = {
  id: string; key: string; size: number; status: string; reason: string
  stats: { read?: number; loaded?: number; updated?: number; duplicates?: number; rejected?: number; warnings?: string[]; rejects_at?: string | null; reject_samples?: { row: number; reason: string }[] }
}
type RunDetail = Run & { objects: Obj[] }
type Source = {
  id: string; name: string; dataset: string; dataset_label: string; root: string; prefix: string; pattern: string; format: string
  mapping: Record<string, string | null>; mapping_version: number; rules: Record<string, number | string | null>; schedule_minutes: number | null
  enabled: boolean; last_run_at: string | null; location?: string; provider?: string; objects: number | null; waiting: number | null
  reachable: boolean; problem?: string; stale: boolean; due: boolean; last_status: string | null; depends_on: string[]; created_by: string
}
type Overview = {
  locations: { id: string; label: string; url: string; provider: string }[]
  datasets: { id: string; label: string; depends_on: string[]; fields: { id: string; required: boolean; desc: string }[] }[]
  rules: { id: string; default: number | string | null; desc: string }[]
  sources: Source[]; runs: Run[]
}
type Preview = {
  location: string; matching: number; bytes: number; objects: { key: string; size: number; modified: string | null }[]
  columns: { column: string; null_rate: number; distinct: number; examples: string[] }[]; proposals: Proposal[]; sampled: string | null; problem: string | null
}

const STATUS: Record<string, [string, string]> = {
  succeeded: ['good', 'Loaded'], succeeded_with_rejects: ['warning', 'Loaded, some records rejected'], partial: ['serious', 'Loaded, some files held'],
  quarantined: ['critical', 'All files held'], failed: ['critical', 'Failed'], nothing_new: ['neutral', 'Nothing new'], skipped: ['warning', 'Skipped'],
  running: ['human', 'Running'],
}
const Status = ({ s }: { s: string | null }) => s ? <Pill tone={STATUS[s]?.[0]}>{STATUS[s]?.[1] ?? s}</Pill> : <span className="muted">Never run</span>
const every = (m: number | null) => m === null ? 'On demand' : m % 1440 === 0 ? `Every ${m / 1440 === 1 ? 'day' : `${m / 1440} days`}` : m % 60 === 0 ? `Every ${m / 60 === 1 ? 'hour' : `${m / 60} hours`}` : `Every ${m} min`
const size = (b: number) => b >= 1e6 ? `${(b / 1e6).toFixed(1)} MB` : b >= 1e3 ? `${Math.round(b / 1e3)} KB` : `${b} B`
const n = (v?: number) => (v ?? 0).toLocaleString('en-US')

function RunView({ run }: { run: Run }) {
  const { data } = useApi<RunDetail>(`/data/runs/${run.id}`)
  const st = run.stats
  const held = data?.objects.filter(o => o.status === 'quarantined') ?? []
  const withRejects = data?.objects.filter(o => o.status === 'loaded' && (o.stats.rejected ?? 0) > 0) ?? []
  return (
    <div className="run-detail">
      <ol className="steps">{run.steps.map((s, i) => <li key={i} className={s.status}><b>{s.step}</b><span>{s.detail}</span></li>)}</ol>
      {run.error && <p className="verdict bad small">{run.error}</p>}
      {st.read !== undefined && run.status !== 'nothing_new' && run.status !== 'skipped' && (
        <div className="kpis tight">
          <Kpi label="Files" value={`${n(st.objects_loaded)} loaded`} sub={`${n(st.objects_quarantined)} held · ${n(st.objects_found)} in the location`} bad={!!st.objects_quarantined} />
          <Kpi label="Records read" value={n(st.read)} sub={st.reconciled ? 'All accounted for' : 'Does not reconcile'} bad={!st.reconciled} />
          <Kpi label="Loaded" value={n(st.loaded)} sub={`${n(st.updated)} updated · ${n(st.duplicates)} already present`} />
          <Kpi label="Rejected" value={n(st.rejected)} bad={!!st.rejected} />
          {!!st.source_amount && <Kpi label="Value loaded" value={fmtMoney(st.loaded_amount ?? 0)} sub={`${fmtMoney(st.source_amount)} in loaded files`} />}
          {(st.alerts ?? 0) > 0 && <Kpi label="Detection" value={`${st.alerts} alerts`} sub={<Link to="/cases">{st.cases} cases opened or updated</Link>} />}
        </div>)}
      {held.length > 0 && <><h3>Held back: nothing from these files was loaded</h3>
        <table><thead><tr><th>File</th><th>Why</th><th>What was wrong</th></tr></thead>
          <tbody>{held.map(o => <tr key={o.id}><td><code>{o.key}</code><div className="muted small">{size(o.size)}</div></td><td>{o.reason}</td>
            <td className="small">{[...new Set(o.stats.reject_samples?.map(r => r.reason))].slice(0, 4).join(' · ') || '–'}
              {o.stats.rejects_at && <div className="muted">Rejected records kept at <code>{o.stats.rejects_at}</code></div>}</td></tr>)}</tbody></table></>}
      {withRejects.length > 0 && <><h3>Loaded with a few records rejected</h3>
        <table><thead><tr><th>File</th><th className="num">Read</th><th className="num">Rejected</th><th>Reasons</th></tr></thead>
          <tbody>{withRejects.map(o => <tr key={o.id}><td><code>{o.key}</code></td><td className="num">{n(o.stats.read)}</td><td className="num">{n(o.stats.rejected)}</td>
            <td className="small">{o.stats.reject_samples?.slice(0, 3).map(r => `row ${r.row}: ${r.reason}`).join(' · ')}</td></tr>)}</tbody></table></>}
      {data?.objects.some(o => o.stats.warnings?.length) && <p className="small muted">Warnings: {[...new Set(data.objects.flatMap(o => o.stats.warnings ?? []))].join(' · ')}</p>}
      <p className="small muted">Run {run.id} · mapping version {run.mapping_version} · {run.trigger === 'schedule' ? 'scheduled' : `started by ${run.started_by}`}
        {data && data.objects.length > 0 ? ` · ${data.objects.length} file(s) handled` : ''}</p>
    </div>
  )
}

function NewSource({ o, done }: { o: Overview; done: () => void }) {
  const [f, setF] = useState({ name: '', dataset: 'transactions', root: o.locations[0]?.id ?? '', prefix: '', pattern: '**', format: 'auto' })
  const [rules, setRules] = useState({ max_reject_rate: '5', min_rows: '1', max_age_hours: '', unknown_customer: 'reject' })
  const [schedule, setSchedule] = useState('60')
  const [seen, setSeen] = useState<Preview | null>(null)
  const [mapping, setMapping] = useState<Record<string, string | null>>({})
  const [err, setErr] = useState<string | null>(null)
  const set = (k: string, v: string) => { setF(x => ({ ...x, [k]: v })); setSeen(null) }
  const look = () => api.post<Preview>('/data/preview', f).then(p => { setSeen(p); setErr(null); setMapping(Object.fromEntries(p.proposals.map(x => [x.target, x.source]))) }).catch(e => setErr(e.message))
  const save = () => api.post('/data/sources', {
    ...f, mapping, columns: seen?.columns.map(c => c.column) ?? [], schedule_minutes: schedule ? Number(schedule) : null,
    rules: { max_reject_rate: Number(rules.max_reject_rate) / 100, min_rows: Number(rules.min_rows), max_age_hours: rules.max_age_hours ? Number(rules.max_age_hours) : null, unknown_customer: rules.unknown_customer },
  }).then(done).catch(e => setErr(e.message))
  const missing = seen?.proposals.filter(p => p.required && !mapping[p.target]).map(p => p.target) ?? []
  return (
    <Card title="Add a source">
      <div className="form">
        <label><span className="lbl">Name</span><input type="search" value={f.name} onChange={e => setF({ ...f, name: e.target.value })} placeholder="Card processor settlements" /></label>
        <label><span className="lbl">What it holds</span><select value={f.dataset} onChange={e => set('dataset', e.target.value)}>{o.datasets.map(d => <option key={d.id} value={d.id}>{d.label}</option>)}</select></label>
        <label><span className="lbl">Storage location</span><select value={f.root} onChange={e => set('root', e.target.value)}>{o.locations.map(l => <option key={l.id} value={l.id}>{l.label} · {l.provider}</option>)}</select></label>
        <label><span className="lbl">Folder inside it</span><input type="search" value={f.prefix} onChange={e => set('prefix', e.target.value)} placeholder="core-banking/transactions" /></label>
        <label><span className="lbl">Files to read</span><input type="search" value={f.pattern} onChange={e => set('pattern', e.target.value)} placeholder="**/part-*" /></label>
        <label><span className="lbl">Format</span><select value={f.format} onChange={e => set('format', e.target.value)}>{['auto', 'csv', 'jsonl', 'parquet'].map(x => <option key={x} value={x}>{x === 'auto' ? 'From the file name' : x}</option>)}</select></label>
      </div>
      <button onClick={look}>Look at this location</button>
      {err && <p className="msg" role="alert">{err}</p>}
      {seen && <>
        <p><b>{seen.matching}</b> matching file{seen.matching === 1 ? '' : 's'} ({size(seen.bytes)}) in <code>{seen.location}</code>
          {seen.sampled && <>. Columns read from the newest, <code>{seen.sampled}</code>.</>}</p>
        {seen.problem && <p className="verdict bad small">{seen.problem}</p>}
        {seen.proposals.length > 0 && <>
          <div className="scroll"><table>
            <thead><tr><th>Field</th><th>Column in the files</th><th>Why proposed</th><th>Examples</th></tr></thead>
            <tbody>{seen.proposals.map(p => (
              <tr key={p.target}><td><b>{p.target}</b>{p.required && <span className="req"> required</span>}<div className="muted small">{p.desc}</div></td>
                <td><select value={mapping[p.target] ?? ''} onChange={e => setMapping({ ...mapping, [p.target]: e.target.value || null })} aria-label={`Column for ${p.target}`}>
                  <option value="">Not mapped</option>{seen.columns.map(c => <option key={c.column}>{c.column}</option>)}</select></td>
                <td className="small">{p.source ? `${p.reason} (${Math.round(p.confidence * 100)}%)` : <span className="muted">no close match</span>}</td>
                <td className="small muted">{seen.columns.find(c => c.column === mapping[p.target])?.examples.join(', ')}</td></tr>))}</tbody>
          </table></div>
          <h3>Quality rules and schedule</h3>
          <div className="form">
            <label><span className="lbl">Hold a file when more than this % of records are unusable</span><input type="search" value={rules.max_reject_rate} onChange={e => setRules({ ...rules, max_reject_rate: e.target.value })} /></label>
            <label><span className="lbl">Flag the source stale after (hours without a new file)</span><input type="search" value={rules.max_age_hours} onChange={e => setRules({ ...rules, max_age_hours: e.target.value })} placeholder="No limit" /></label>
            <label><span className="lbl">Records for a customer not loaded yet</span><select value={rules.unknown_customer} onChange={e => setRules({ ...rules, unknown_customer: e.target.value })}><option value="reject">Reject the record</option><option value="create">Create a placeholder customer</option></select></label>
            <label><span className="lbl">Run every (minutes; empty for on demand)</span><input type="search" value={schedule} onChange={e => setSchedule(e.target.value)} /></label>
          </div>
          <button className="primary" disabled={missing.length > 0 || !f.name.trim()} onClick={save}>Save source</button>
          {missing.length > 0 && <span className="msg"> Map the required fields: {missing.join(', ')}</span>}
        </>}
      </>}
    </Card>
  )
}

export default function Data() {
  const { can, holder } = usePeople()
  const canLoad = can('load_data')
  const { data, error, reload } = useApi<Overview>('/data')
  const [open, setOpen] = useState<string | null>(null)
  const [detail, setDetail] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const run = (path: string, body?: unknown) => {
    setBusy(true); setMsg(null)
    api.post<{ runs?: Run[] } & Partial<Run>>(path, body).then(r => { const runs = r.runs ?? [r as Run]; setOpen(runs[runs.length - 1]?.id ?? null); if (!runs.length) setMsg('No source is due.'); reload() })
      .catch(e => setMsg(e.message)).finally(() => setBusy(false))
  }
  const waiting = data.sources.reduce((a, s) => a + (s.waiting ?? 0), 0)
  const last = data.runs[0]
  const batch = last ? data.runs.filter(r => r.batch_id === last.batch_id) : []
  return (
    <>
      <div className="page-h case-h"><div><h1>Data</h1>
        <p>Pipelines that read each source system's files from your object storage, check them, and load what passes. Nothing is uploaded by hand.</p></div>
        <div className="inline">
          <button className="primary" disabled={!canLoad || busy} onClick={() => run('/data/run', {})}>{busy ? 'Running…' : 'Run all sources'}</button>
          <button disabled={!canLoad || busy} onClick={() => run('/data/run', { due_only: true })}>Run what is due</button>
          <button disabled={!canLoad} onClick={() => setAdding(a => !a)}>{adding ? 'Close' : 'Add a source'}</button>
        </div></div>
      {!canLoad && <p className="muted small">You can see pipelines and their runs. Running and changing them needs the load data permission, held by {holder('load_data')}.</p>}
      {msg && <p className="msg" role="status">{msg}</p>}
      <div className="kpis">
        <Kpi label="Sources" value={data.sources.length} sub={`${data.sources.filter(s => s.enabled && s.schedule_minutes).length} on a schedule`} />
        <Kpi label="New files waiting" value={waiting} sub={waiting ? 'Not yet loaded' : 'Everything is loaded'} />
        <Kpi label="Last batch" value={last ? fmtDateTime(last.started + 'Z') : '–'} sub={last ? `${n(batch.reduce((a, r) => a + (r.stats.loaded ?? 0), 0))} records loaded` : 'No runs yet'} />
        <Kpi label="Files held back" value={batch.reduce((a, r) => a + (r.stats.objects_quarantined ?? 0), 0)} sub="In the last batch" bad={batch.some(r => r.stats.objects_quarantined)} />
      </div>
      {adding && <NewSource o={data} done={() => { setAdding(false); reload() }} />}
      <Card title="Sources" action={<span className="muted small">Loaded in order: {data.datasets.map(d => d.label).join(' → ')}</span>}>
        {data.sources.length === 0 ? <p className="empty">No sources yet. Add one to start reading files from a storage location.</p> : <div className="scroll"><table>
          <thead><tr><th>Source</th><th>Reads from</th><th className="num">Files</th><th>Schedule</th><th>Last run</th><th></th></tr></thead>
          <tbody>{data.sources.map(s => (
            <Fragment key={s.id}>
              <tr>
                <td><button className="ghost link" onClick={() => setDetail(detail === s.id ? null : s.id)} aria-expanded={detail === s.id}><b>{s.name}</b></button>
                  <div className="muted small">{s.dataset_label}{s.depends_on.length ? ` · after ${s.depends_on.join(' and ')}` : ''}</div></td>
                <td><Pill>{s.provider}</Pill><div className="small"><code>{s.location}</code></div><div className="muted small">{s.pattern}{s.format !== 'auto' ? ` · ${s.format}` : ''}</div></td>
                <td className="num">{s.reachable ? <>{s.waiting ? <Pill tone="human">{s.waiting} new</Pill> : null}<div className="muted small">{s.objects} in all</div></> : <Pill tone="critical">Unreachable</Pill>}</td>
                <td>{every(s.schedule_minutes)}{s.due && <div><Pill tone="warning">Due</Pill></div>}{s.stale && <div><Pill tone="serious">Stale</Pill></div>}</td>
                <td><Status s={s.last_status} />{s.last_run_at && <div className="muted small">{fmtDateTime(s.last_run_at + 'Z')}</div>}</td>
                <td><button disabled={!canLoad || busy} onClick={() => run(`/data/sources/${s.id}/run`)}>Run</button></td>
              </tr>
              {detail === s.id && <tr className="hit"><td colSpan={6}>
                <div className="cols-2">
                  <div><h3>Mapping, version {s.mapping_version}</h3><dl className="facts">{Object.entries(s.mapping).map(([k, v]) => <Fragment key={k}><dt>{k}</dt><dd>{v ? <code>{v}</code> : <span className="muted">not mapped</span>}</dd></Fragment>)}</dl></div>
                  <div><h3>Quality rules</h3><ul className="plain">
                    <li>A file is held when more than <b>{Math.round(Number(s.rules.max_reject_rate) * 100)}%</b> of its records are unusable.</li>
                    <li>A file needs at least <b>{s.rules.min_rows}</b> record{s.rules.min_rows === 1 ? '' : 's'}.</li>
                    <li>{s.rules.max_age_hours ? <>Flagged stale after <b>{s.rules.max_age_hours} hours</b> without a new file.</> : 'No freshness limit.'}</li>
                    <li>Records for a customer not yet loaded are <b>{s.rules.unknown_customer === 'reject' ? 'rejected' : 'given a placeholder customer'}</b>.</li>
                    <li className="muted small">Set up by {s.created_by}. A file is identified by its name and content fingerprint, so a run never loads it twice.</li></ul></div>
                </div></td></tr>}
            </Fragment>))}</tbody>
        </table></div>}
      </Card>
      <Card title="Runs">
        {data.runs.length === 0 ? <p className="empty">No runs yet. {waiting} file{waiting === 1 ? ' is' : 's are'} waiting in the landing zone: run all sources to load them.</p> : <table className="runs">
          <thead><tr><th>Started</th><th>Source</th><th>Result</th><th className="num">Files</th><th className="num">Loaded</th><th className="num">Rejected</th><th></th></tr></thead>
          <tbody>{data.runs.map(r => (
            <Fragment key={r.id}>
              <tr><td className="nowrap">{fmtDateTime(r.started + 'Z')}</td><td>{r.source}</td><td><Status s={r.status} /></td>
                <td className="num">{n(r.stats.objects_loaded)}{r.stats.objects_quarantined ? <> <Pill tone="critical">{r.stats.objects_quarantined} held</Pill></> : null}</td>
                <td className="num">{n(r.stats.loaded)}</td><td className="num">{n(r.stats.rejected)}</td>
                <td><button className="ghost" onClick={() => setOpen(open === r.id ? null : r.id)} aria-expanded={open === r.id}>{open === r.id ? 'Hide' : 'Details'}</button></td></tr>
              {open === r.id && <tr className="hit"><td colSpan={7}><RunView run={r} /></td></tr>}
            </Fragment>))}</tbody>
        </table>}
      </Card>
      <div className="cols-2">
        <Card title="Storage locations">
          <table><tbody>{data.locations.map(l => <tr key={l.id}><td><b>{l.label}</b><div className="muted small">{l.id}</div></td><td><Pill>{l.provider}</Pill></td><td className="small"><code>{l.url}</code></td></tr>)}</tbody></table>
          <p className="small muted">A source can only read from locations registered for this client by the platform operator. The same pipeline reads Amazon S3 (<code>s3://</code>), Azure Data Lake (<code>abfs://</code>), Google Cloud Storage (<code>gs://</code>) or a directory; this demo uses a directory. No credentials are stored: the platform's own cloud identity is granted access.</p>
        </Card>
        <Card title="Trying out a file by hand">
          <p>To explore an extract before a pipeline exists, profile and load a sample file by hand. This is for samples only; production data arrives through the sources above.</p>
          <Link to="/onboarding">Open the sample file loader</Link>
        </Card>
      </div>
    </>
  )
}
