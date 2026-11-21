import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDate, words } from '../../api'
import { useNameOf } from '../../App'
import { Pill } from '../ui'
import { show, type Desk, type Lookback, type Scenario } from './types'

const TONE: Record<string, string> = { planned: 'warning', working: 'human', closed: 'good', cancelled: 'neutral' }
const LABEL: Record<string, string> = { planned: 'Planned, not yet run', working: 'Being worked', closed: 'Signed off', cancelled: 'Cancelled' }
const day = (iso: string) => new Date(iso).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })

function One({ lb, desk, onChange }: { lb: Lookback; desk: Desk; onChange: (m: string) => void }) {
  const nameOf = useNameOf()
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const step = (name: string, ok: string) => { setBusy(true); api.post(`/lookbacks/${lb.id}/${name}`, { note }).then(() => { setNote(''); setErr(null); onChange(ok) }).catch(e => setErr(e.message)).finally(() => setBusy(false)) }
  const changed = Object.keys(lb.params).filter(k => lb.params[k] !== lb.defaults[k])
  const p = lb.progress, pv = lb.preview
  const limits = desk.scenarios.find(s => s.scenario === lb.scenario)?.limits ?? {}
  return (
    <div className={`hold ${lb.status === 'working' ? 'active' : 'released'}`}>
      <div className="hold-h"><b>{lb.name}</b><Pill tone={TONE[lb.status]}>{LABEL[lb.status]}</Pill>
        {lb.overdue && <Pill tone="critical">Past its date, {fmtDate(lb.due!)}</Pill>}
        {lb.status !== 'planned' && <span className="push"><button className="ghost" onClick={() => api.download(`/lookbacks/${lb.id}/report`).catch(e => setErr(e.message))}>Download report</button></span>}</div>
      <p className="small">{lb.reason}</p>
      <dl className="facts dense">
        <dt>Scenario</dt><dd>{lb.scenario_title}</dd>
        <dt>Settings</dt><dd>{changed.length ? changed.map(k => `${limits[k]?.label ?? words(k)}: ${show(k, lb.params[k])} instead of ${show(k, lb.defaults[k])}`).join('; ') : 'The ordinary settings'}</dd>
        <dt>Period</dt><dd>{day(lb.period_from)} to {day(lb.period_to)}{lb.risk_ratings.length > 0 && `, ${lb.risk_ratings.join(' and ')} risk customers only`}</dd>
        <dt>Planned by</dt><dd>{nameOf(lb.created_by)}, {fmtDate(lb.created)}{lb.due && ` · due ${day(lb.due)}`}</dd>
      </dl>
      {lb.status === 'planned' && <>
        <div className="kpis slim">
          <div className="kpi"><span>Found in the period</span><b>{pv.found}</b><small>on these settings</small></div>
          <div className="kpi"><span>Already alerted at the time</span><b>{pv.already_alerted}</b><small>{pv.already.productive ?? 0} led to a filing or confirmed concern</small></div>
          <div className="kpi"><span>Running it would raise</span><b className={pv.over_limit ? 'bad' : ''}>{pv.to_raise}</b><small>{pv.to_raise ? `alert${pv.to_raise === 1 ? '' : 's'} on ${pv.customers} customer${pv.customers === 1 ? '' : 's'}` : 'nothing new'}</small></div>
        </div>
        {pv.sample.length > 0 && <details><summary className="small">What it would raise</summary>
          <table><tbody>{pv.sample.map((x, i) => <tr key={i}><td><Link to={`/customers/${x.party_id}`}>{x.party_name}</Link></td><td className="small">{x.explanation}</td><td className="num">{x.score.toFixed(2)}</td></tr>)}</tbody></table></details>}
        {pv.over_limit && <p className="msg">One run may raise {desk.lookback_limit} alerts. Split this by period or by customer risk rating.</p>}
        {desk.can_propose && <div className="hold-act"><div className="inline wrap">
          <button className="primary" disabled={busy || pv.over_limit || pv.to_raise === 0} onClick={() => step('run', 'Lookback run. Its alerts are in cases, waiting for people.')}>Run it: raise {pv.to_raise} alert{pv.to_raise === 1 ? '' : 's'}</button>
          <button disabled={busy} onClick={() => step('count', 'Counted again.')}>Count again</button>
          <input type="search" aria-label="Why cancel" placeholder="Why cancel" value={note} onChange={e => setNote(e.target.value)} />
          <button disabled={note.trim().length < 10} onClick={() => step('cancel', 'Lookback cancelled.')}>Cancel it</button></div>
          <p className="muted small">Every alert goes to a person: no agent closes a lookback alert and no rule lets it wait.</p></div>}
      </>}
      {lb.status !== 'planned' && lb.status !== 'cancelled' && <>
        <div className="lb-prog" role="img" aria-label={`${p.closed} of ${p.cases} cases decided`}><div><i style={{ width: `${100 * p.complete}%` }} /></div><span><b>{p.closed}</b> of {p.cases} cases decided</span></div>
        <div className="kpis slim">
          <div className="kpi"><span>Raised by this lookback</span><b>{p.alerts}</b><small>on {p.customers} customer{p.customers === 1 ? '' : 's'}; {pv.already_alerted} had been alerted at the time</small></div>
          <div className="kpi"><span>Cases still open</span><b className={lb.overdue && p.open ? 'bad' : ''}>{p.open}</b><small>of {p.cases}</small></div>
          <div className="kpi"><span>Led to a filing or confirmed concern</span><b>{p.productive}</b><small>{p.filings} report{p.filings === 1 ? '' : 's'} filed</small></div>
        </div>
        {p.case_ids.length > 0 && <p className="small">Cases: {p.case_ids.map(c => <Link key={c} to={`/cases/${c}`} style={{ marginRight: 8 }}>{c}</Link>)}</p>}
        {lb.status === 'closed' && <p className="answer">{lb.conclusion}<span className="muted small"> signed off by {nameOf(lb.closed_by!)}, {fmtDate(lb.closed_at!)}</span></p>}
        {lb.status === 'working' && desk.can_sign_off && <div className="hold-act">
          <textarea rows={2} aria-label="Conclusion" placeholder="Conclusion: what was found, what was filed and what changes as a result" value={note} onChange={e => setNote(e.target.value)} />
          <div className="inline"><button className="primary" disabled={p.open > 0 || note.trim().length < 40 || lb.run_by === desk.me} onClick={() => step('close', 'Lookback signed off.')}>Sign off</button>
            <span className="muted small">{p.open > 0 ? `${p.open} case${p.open === 1 ? ' is' : 's are'} still open.` : lb.run_by === desk.me ? 'You ran it, so someone else signs it off.' : 'The figures are frozen as they stand when you sign.'}</span></div></div>}
      </>}
      <details><summary className="small">{lb.history.length} step{lb.history.length === 1 ? '' : 's'} on record</summary>
        <ol className="custody">{lb.history.map((x, i) => <li key={i}><b>{x.what}</b> by {nameOf(x.by)}, {fmtDate(x.at)}{x.note && `: ${x.note}`}</li>)}</ol></details>
      {err && <p className="msg" role="alert">{err}</p>}
    </div>
  )
}

const iso = (d: Date) => d.toISOString().slice(0, 10)
const blank = (sc: Scenario | undefined) => ({ name: '', reason: '', scenario: sc?.scenario ?? '', params: Object.fromEntries(Object.keys(sc?.limits ?? {}).map(k => [k, String(sc!.params[k])])),
  period_from: iso(new Date(Date.now() - 180 * 86400000)), period_to: iso(new Date(Date.now() - 86400000)), risk_ratings: [] as string[], due: '' })

/** Lookback projects: plan one with a count of what it would raise, run it, and follow its cases to a signed conclusion. */
export default function Lookbacks({ desk, onChange }: { desk: Desk; onChange: (m: string) => void }) {
  const tunable = desk.scenarios.filter(s => s.tunable)
  const [form, setForm] = useState<ReturnType<typeof blank> | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const sc = tunable.find(s => s.scenario === form?.scenario)
  const save = () => form && api.post('/lookbacks', { ...form, params: Object.fromEntries(Object.entries(form.params).map(([k, v]) => [k, Number(v)])), due: form.due || null })
    .then(() => { setForm(null); setErr(null); onChange('Lookback planned. Nothing is raised until it is run.') }).catch(e => setErr(e.message))
  return (
    <>
      {desk.can_propose && !form && <div className="inline"><button onClick={() => setForm(blank(tunable[0]))}>Plan a lookback…</button></div>}
      {form && sc && <div className="override-form">
        <div className="inline wrap">
          <input type="search" aria-label="Name" placeholder="Name" value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} />
          <select aria-label="Scenario" value={form.scenario} onChange={e => setForm({ ...blank(tunable.find(s => s.scenario === e.target.value)), name: form.name, reason: form.reason })}>
            {tunable.map(s => <option key={s.scenario} value={s.scenario}>{s.title}</option>)}</select>
        </div>
        <textarea rows={2} aria-label="Why" placeholder="Why it is being done: a regulator's finding, a defect, a new typology" value={form.reason} onChange={e => setForm({ ...form, reason: e.target.value })} />
        <table className="factors"><tbody>{Object.entries(sc.limits).map(([k, l]) => <tr key={k}><td>{l.label}</td>
          <td><input type="number" aria-label={l.label} min={l.min} max={l.max} step={l.whole ? 1 : 0.05} value={form.params[k]} onChange={e => setForm({ ...form, params: { ...form.params, [k]: e.target.value } })} />
            <span className="muted small"> in force {show(k, sc.params[k])}</span></td></tr>)}</tbody></table>
        <div className="inline wrap">
          <label className="small">From <input type="date" value={form.period_from} onChange={e => setForm({ ...form, period_from: e.target.value })} /></label>
          <label className="small">To <input type="date" value={form.period_to} onChange={e => setForm({ ...form, period_to: e.target.value })} /></label>
          <label className="small">Due <input type="date" value={form.due} onChange={e => setForm({ ...form, due: e.target.value })} /></label>
          {['low', 'medium', 'high'].map(r => <label key={r} className="small"><input type="checkbox" checked={form.risk_ratings.includes(r)}
            onChange={e => setForm({ ...form, risk_ratings: e.target.checked ? [...form.risk_ratings, r] : form.risk_ratings.filter(x => x !== r) })} /> {r} risk</label>)}
        </div>
        <div className="inline"><button className="primary" onClick={save}>Plan and count</button><button onClick={() => { setForm(null); setErr(null) }}>Cancel</button>
          <span className="muted small">No rating ticked means every customer. Planning only counts; nothing is raised until it is run.</span></div>
        {err && <p className="msg" role="alert">{err}</p>}
      </div>}
      {desk.lookbacks.length === 0 && !form ? <p className="empty">No lookback has been planned.</p> : desk.lookbacks.map(lb => <One key={lb.id} lb={lb} desk={desk} onChange={onChange} />)}
    </>
  )
}
