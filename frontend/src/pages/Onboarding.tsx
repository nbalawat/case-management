import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDateTime, fmtMoney } from '../api'
import { usePeople } from '../shell/people'
import { useApi } from '../useApi'
import { Card, Loading, Pill } from '../components/ui'

type Proposal = { target: string; source: string | null; confidence: number; required: boolean; reason: string; samples: string[]; desc: string }
type Profile = { name: string; columns: { column: string; null_rate: number; distinct: number; examples: string[] }[]; proposals: Proposal[]; preview: Record<string, string>[] }
type Stats = { read: number; loaded: number; duplicates: number; rejected: number; source_amount: number; loaded_amount: number; reconciled: boolean; reject_samples: { row: number; reason: string }[] }
type Result = { feed: { id: string; name: string; status: string; stats: Stats }; new_parties: number; alerts: number; cases: { id: string; party_name: string; priority: string }[] }
type Feeds = { feeds: { id: string; name: string; status: string; created: string; stats: Stats }[]; samples: string[] }

export default function Onboarding() {
  const { can, holder } = usePeople()
  const canLoad = can('load_data')
  const feeds = useApi<Feeds>('/feeds')
  const [sample, setSample] = useState('legacy_core_transactions.csv')
  const [upload, setUpload] = useState<{ name: string; text: string } | null>(null)
  const [prof, setProf] = useState<Profile | null>(null)
  const [mapping, setMapping] = useState<Record<string, string | null>>({})
  const [result, setResult] = useState<Result | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const src = upload ? { name: upload.name, text: upload.text } : { sample }
  const step = result ? 3 : prof ? 2 : 1

  const doProfile = async () => {
    setBusy(true); setErr(null); setResult(null)
    try {
      const p = await api.post<Profile>('/feeds/profile', src)
      setProf(p); setMapping(Object.fromEntries(p.proposals.map(x => [x.target, x.source])))
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const doRun = async () => {
    setBusy(true); setErr(null)
    try { setResult(await api.post<Result>('/feeds/run', { ...src, mapping })); feeds.reload() }
    catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const missing = prof?.proposals.filter(p => p.required && !mapping[p.target]).map(p => p.target) ?? []

  return (
    <>
      <div className="crumbs"><Link to="/data">Data</Link> / Sample file loader</div>
      <div className="page-h"><h1>Sample file loader</h1><p>Explore an extract by hand: profile it, confirm the proposed mapping, load and reconcile. For samples only. Production data arrives through <Link to="/data">pipelines from object storage</Link>.</p></div>
      <ol className="stepper"><li className={step > 1 ? 'done' : 'now'}>Connect and profile</li><li className={step > 2 ? 'done' : step === 2 ? 'now' : ''}>Confirm mapping</li><li className={step === 3 ? 'now' : ''}>Load, reconcile, detect</li></ol>

      <Card title="1. Connect a source">
        <div className="toolbar">
          <select value={upload ? '' : sample} onChange={e => { setUpload(null); setSample(e.target.value); setProf(null); setResult(null) }} aria-label="Sample feed">
            {upload && <option value="">{upload.name}</option>}
            {(feeds.data?.samples ?? [sample]).map(s => <option key={s} value={s}>Sample: {s}</option>)}
          </select>
          <label className="file">Upload CSV<input type="file" accept=".csv,text/csv" onChange={async e => {
            const f = e.target.files?.[0]; if (f) { setUpload({ name: f.name, text: await f.text() }); setProf(null); setResult(null) } }} /></label>
          <button className="primary" onClick={doProfile} disabled={busy}>Profile feed</button>
        </div>
        <p className="muted small">The sample is an extract from a fictional legacy core with its own column names, code lists and a few defective rows.</p>
      </Card>

      {err && <p className="msg" role="alert">{err}</p>}
      {busy && <Loading />}

      {prof && <Card title="2. Proposed mapping to the canonical model" action={<span className="muted small">{prof.columns.length} source columns profiled</span>}>
        <div className="scroll"><table>
          <thead><tr><th>Canonical field</th><th>Source column</th><th>Confidence</th><th>Why</th><th>Sample values</th></tr></thead>
          <tbody>{prof.proposals.map(p => (
            <tr key={p.target}>
              <td><b>{p.target}</b>{p.required && <span className="req"> required</span>}<div className="muted small">{p.desc}</div></td>
              <td><select value={mapping[p.target] ?? ''} onChange={e => setMapping({ ...mapping, [p.target]: e.target.value || null })} aria-label={`Source for ${p.target}`}>
                <option value="">Not mapped</option>{prof.columns.map(c => <option key={c.column} value={c.column}>{c.column}</option>)}</select></td>
              <td>{p.source ? <div className="conf-bar" title={`${Math.round(p.confidence * 100)}%`}><div style={{ width: `${p.confidence * 100}%` }} /><span>{Math.round(p.confidence * 100)}%</span></div> : <Pill tone="warning">review</Pill>}</td>
              <td className="muted">{p.reason}</td>
              <td className="muted small">{(prof.columns.find(c => c.column === mapping[p.target])?.examples ?? []).join(' · ')}</td>
            </tr>))}</tbody>
        </table></div>
        <div className="toolbar">
          <button className="primary" onClick={doRun} disabled={busy || missing.length > 0 || !canLoad} title={canLoad ? '' : 'Loading data needs the platform admin'}>Load and run detection</button>
          {!canLoad && <span className="msg">Sign in as {holder('load_data')} to load data.</span>}
          {missing.length > 0 ? <span className="msg">Map required fields: {missing.join(', ')}</span>
            : <span className="muted small">Unmapped source columns are kept in the raw zone and not loaded.</span>}
        </div>
      </Card>}

      {result && <Card title="3. Reconciliation and detection">
        <div className="kpis">
          <div className="kpi"><span>Records read</span><b>{result.feed.stats.read.toLocaleString()}</b></div>
          <div className="kpi"><span>Loaded</span><b>{result.feed.stats.loaded.toLocaleString()}</b><small>{fmtMoney(result.feed.stats.loaded_amount)}</small></div>
          <div className="kpi"><span>Rejected</span><b className={result.feed.stats.rejected ? 'bad' : ''}>{result.feed.stats.rejected}</b><small>{result.feed.stats.duplicates} duplicates skipped</small></div>
          <div className="kpi"><span>Count reconciliation</span><b>{result.feed.stats.reconciled ? 'Balanced' : 'Gap'}</b><small>read = loaded + rejected + duplicates</small></div>
          <div className="kpi"><span>New customers</span><b>{result.new_parties}</b></div>
          <div className="kpi"><span>Alerts raised</span><b>{result.alerts}</b><small>{result.cases.length} cases opened</small></div>
        </div>
        {result.feed.stats.reject_samples.length > 0 && <details><summary><b>Rejected rows</b> <span className="muted">held for repair, not silently dropped</span></summary>
          <ul className="findings">{result.feed.stats.reject_samples.map(r => <li key={r.row}>Row {r.row}: {r.reason}</li>)}</ul></details>}
        {result.cases.length > 0 && <><h3>Cases opened from this feed</h3>
          <ul className="findings">{result.cases.map(c => <li key={c.id}><Link to={`/cases/${c.id}`}>{c.party_name}</Link> <Pill>{c.priority}</Pill> agent briefing ready</li>)}</ul></>}
        {result.alerts === 0 && result.feed.stats.loaded === 0 && <p className="empty">Nothing new: this feed was already loaded, so every row was a duplicate.</p>}
      </Card>}

      <Card title="Feed history">
        {!feeds.data ? <Loading error={feeds.error} /> : feeds.data.feeds.length === 0 ? <p className="empty">No feeds loaded yet. Profile the sample above to start.</p> :
          <table><thead><tr><th>Feed</th><th>Status</th><th className="num">Read</th><th className="num">Loaded</th><th className="num">Rejected</th><th>Loaded at</th></tr></thead>
            <tbody>{feeds.data.feeds.map(f => <tr key={f.id}><td>{f.name}<div className="muted small">{f.id}</div></td><td>{f.status.replaceAll('_', ' ')}</td>
              <td className="num">{f.stats.read}</td><td className="num">{f.stats.loaded}</td><td className="num">{f.stats.rejected}</td><td>{fmtDateTime(f.created)}</td></tr>)}</tbody></table>}
      </Card>
    </>
  )
}
