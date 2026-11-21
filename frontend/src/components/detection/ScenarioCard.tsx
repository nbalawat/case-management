import { useState } from 'react'
import { api, fmtDate } from '../../api'
import { useNameOf } from '../../shell/people'
import { Pill } from '../ui'
import BacktestView from './BacktestView'
import { show, type Backtest, type Desk, type Scenario, type Version } from './types'

const STATUS: Record<string, string> = { live: 'good', superseded: 'neutral', refused: 'neutral', withdrawn: 'neutral', pending: 'warning' }
const changes = (from: Record<string, number>, to: Record<string, number>, sc: Scenario) =>
  Object.keys(sc.limits).filter(k => from[k] !== to[k]).map(k => `${sc.limits[k].label}: ${show(k, from[k])} to ${show(k, to[k])}`)

function Pending({ sc, v, desk, onChange }: { sc: Scenario; v: Version; desk: Desk; onChange: (m: string) => void }) {
  const nameOf = useNameOf()
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const mine = v.proposed_by === desk.me
  const decide = (approve: boolean) => api.post(`/detection/proposals/${v.id}/decision`, { approve, note })
    .then(() => onChange(approve ? 'The change is live. It applies from the next detection run.' : 'Proposal refused.')).catch(e => setErr(e.message))
  const what = [...changes(sc.params, v.params, sc), ...(v.enabled !== sc.enabled ? [v.enabled ? 'Switch the scenario on' : 'Switch the scenario off'] : [])]
  const needsNote = v.backtest.productive_lost > 0 || !v.enabled
  return (
    <div className="override pending">
      <b>Change waiting for a second person</b>
      <ul className="own-flags">{what.map(w => <li key={w}>{w}</li>)}</ul>
      <div className="small">“{v.reason}” <span className="muted">{nameOf(v.proposed_by)}, {fmtDate(v.proposed_at)}</span></div>
      <BacktestView b={v.backtest} />
      {desk.can_decide && !mine && <div className="inline">
        <input type="search" aria-label="Note" placeholder={needsNote ? 'Why this is acceptable (needed to approve)' : 'Note (needed to refuse)'} value={note} onChange={e => setNote(e.target.value)} />
        <button className="primary" disabled={needsNote && note.trim().length < 20} onClick={() => decide(true)}>Approve</button>
        <button disabled={note.trim().length < 10} onClick={() => decide(false)}>Refuse</button></div>}
      {mine && <div className="inline"><span className="muted small">You proposed this, so someone else decides it.</span>
        <button onClick={() => api.post(`/detection/proposals/${v.id}/withdraw`).then(() => onChange('Proposal withdrawn.')).catch(e => setErr(e.message))}>Withdraw</button></div>}
      {err && <p className="msg" role="alert">{err}</p>}
    </div>
  )
}

/** One scenario: what it has raised, the settings in force, and a place to try different ones against the past before proposing them. */
export default function ScenarioCard({ sc, desk, onChange }: { sc: Scenario; desk: Desk; onChange: (m: string) => void }) {
  const nameOf = useNameOf()
  const [draft, setDraft] = useState<Record<string, string> | null>(null)
  const [on, setOn] = useState(sc.enabled)
  const [result, setResult] = useState<Backtest | null>(null)
  const [reason, setReason] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const r = sc.record
  const decided = r.productive + r.unproductive
  const body = () => ({ params: Object.fromEntries(Object.entries(draft ?? {}).map(([k, v]) => [k, Number(v)])), enabled: on, reason })
  const start = () => { setDraft(Object.fromEntries(Object.keys(sc.limits).map(k => [k, String(sc.params[k])]))); setOn(sc.enabled); setResult(null); setErr(null) }
  const run = () => { setBusy(true); api.post<Backtest>(`/detection/scenarios/${sc.scenario}/backtest`, body()).then(b => { setResult(b); setErr(null) }).catch(e => setErr(e.message)).finally(() => setBusy(false)) }
  const propose = () => api.post(`/detection/scenarios/${sc.scenario}/proposals`, body())
    .then(() => { setDraft(null); setResult(null); setReason(''); onChange('Change proposed. It waits for a second person.') }).catch(e => setErr(e.message))
  const edit = (k: string, v: string) => { setDraft({ ...draft, [k]: v }); setResult(null) }
  return (
    <div className={`scn ${sc.enabled ? '' : 'off'}`}>
      <div className="hold-h"><b>{sc.title}</b><span className="muted small">{sc.typology} · v{sc.version}{sc.revision ? ` · this client's settings, revision ${sc.revision}` : ' · default settings'}</span>
        {!sc.enabled && <Pill tone="serious">Switched off</Pill>}
        {sc.tunable && desk.can_propose && !sc.pending && !draft && <span className="push"><button onClick={start}>Try different settings…</button></span>}</div>
      <div className="scn-rec" title={`${r.alerts} alerts: ${r.productive} led to a filing or confirmed concern, ${r.unproductive} closed with nothing found, ${r.undecided} undecided`}>
        <span><b>{r.alerts}</b> alerts raised</span>
        <div className="rstack">{r.productive > 0 && <i className="in" style={{ flex: r.productive }} />}{r.unproductive > 0 && <i className="rest" style={{ flex: r.unproductive }} />}{r.undecided > 0 && <i className="open" style={{ flex: r.undecided }} />}</div>
        <span className="small">{decided ? <><b>{Math.round((r.productive_rate ?? 0) * 100)}%</b> of the {decided} decided led to a filing or confirmed concern</> : 'None decided yet'}{r.undecided > 0 && `, ${r.undecided} still open`}</span>
      </div>
      {!sc.tunable ? <p className="muted small">Its similarity threshold is the client's screening rule, set on Lists.</p> : (
        <table className="factors"><thead><tr><th>Setting</th><th className="num">Default</th><th className="num">In force</th>{draft && <th>Try</th>}</tr></thead>
          <tbody>{Object.entries(sc.limits).map(([k, l]) => <tr key={k}>
            <td>{l.label}</td><td className="num muted">{show(k, sc.defaults[k])}</td><td className="num"><b>{show(k, sc.params[k])}</b></td>
            {draft && <td><input type="number" aria-label={l.label} min={l.min} max={l.max} step={l.whole ? 1 : 0.05} value={draft[k]} onChange={e => edit(k, e.target.value)} />
              <span className="muted small"> {show(k, l.min)} to {show(k, l.max)}</span></td>}
          </tr>)}</tbody></table>)}
      {draft && <div className="override-form">
        <div className="inline wrap"><label className="small"><input type="checkbox" checked={on} onChange={e => { setOn(e.target.checked); setResult(null) }} /> Scenario switched on</label>
          <button onClick={run} disabled={busy}>{busy ? 'Running…' : 'Run against the last year'}</button><button onClick={() => { setDraft(null); setResult(null) }}>Cancel</button></div>
        {result && <>
          <BacktestView b={result} />
          <textarea rows={2} aria-label="Why" placeholder="Why make this change? The approver and audit will read it." value={reason} onChange={e => setReason(e.target.value)} />
          <div className="inline"><button className="primary" disabled={reason.trim().length < 20} onClick={propose}>Propose this change</button>
            <span className="muted small">A second person must approve before it takes effect. It changes what is detected from then on, not what was raised before.</span></div>
        </>}
        {err && <p className="msg" role="alert">{err}</p>}
      </div>}
      {sc.pending && <Pending sc={sc} v={sc.pending} desk={desk} onChange={onChange} />}
      {sc.history.length > 0 && <details><summary className="small">{sc.history.length} earlier decision{sc.history.length === 1 ? '' : 's'}</summary>
        <ol className="custody">{sc.history.map(v => <li key={v.id}><Pill tone={STATUS[v.status]}>{v.status}</Pill> Revision {v.number}: {Object.keys(sc.limits).map(k => `${show(k, v.params[k])}`).join(' / ')}{!v.enabled && ', switched off'}.
          Proposed by {nameOf(v.proposed_by)}, {fmtDate(v.proposed_at)}{v.decided_by && `; ${v.status === 'withdrawn' ? 'withdrawn' : 'decided'} by ${nameOf(v.decided_by)}`}. “{v.reason}”{v.decision_note && ` Answer: “${v.decision_note}”`}</li>)}</ol></details>}
    </div>
  )
}
