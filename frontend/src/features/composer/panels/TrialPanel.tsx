import { useRef, useState } from 'react'
import { CheckCircle2, CircleAlert, FlaskConical, Play, RotateCcw, Undo2 } from 'lucide-react'
import { fmtMoney } from '../../../api'
import { composerApi } from '../api'
import type { Design, Sample, Trial } from '../types'

const START: Sample = { name: 'Sample Customer', amount: 25000, risk_rating: 'medium', pep: false, priority: 'medium' }

/**
 * Try the workflow on a sample case. Each step runs for real through the case core, inside a transaction that is
 * thrown away, so the guards and automatic actions behave exactly as they will once published.
 */
export default function TrialPanel({ design, onHighlight }: { design: Design; onHighlight: (current: string | null, visited: string[]) => void }) {
  const [sample, setSample] = useState<Sample>(START)
  const [steps, setSteps] = useState<string[] | null>(null)
  const [result, setResult] = useState<Trial | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const seq = useRef(0)
  const [ranOn, setRanOn] = useState<Design | null>(null)

  const run = (next: string[] | null, s: Sample = sample) => {
    setSteps(next)
    const n = ++seq.current
    if (next === null) { setResult(null); onHighlight(null, []); return }
    setBusy(true)
    setRanOn(design)
    composerApi.trial(design, s, next).then(r => {
      if (n !== seq.current) return
      setResult(r); setError(null)
      if (r.ready && r.case && r.state_nodes) onHighlight(r.state_nodes[r.case.stage] ?? null, (r.steps ?? []).filter(x => x.ok).map(x => r.state_nodes![x.to]))
    }).catch(e => n === seq.current && setError(e.message)).finally(() => n === seq.current && setBusy(false))
  }
  const stale = steps !== null && ranOn !== null && ranOn !== design

  const reset = () => run(null)
  const set = (patch: Partial<Sample>) => { const s = { ...sample, ...patch }; setSample(s); if (steps !== null) run([], s) }
  return (
    <div className="cf-trial">
      <div className="cf-trial-sample">
        <h4><FlaskConical size={15} />Sample case</h4>
        <div className="cf-row">
          <label>Customer<input value={sample.name} onChange={e => set({ name: e.target.value })} /></label>
          <label>Amount involved<input type="number" min={0} step={1000} value={sample.amount} onChange={e => set({ amount: Number(e.target.value) })} /></label>
        </div>
        <div className="cf-row">
          <label>Customer risk<select value={sample.risk_rating} onChange={e => set({ risk_rating: e.target.value })}>{['low', 'medium', 'high'].map(r => <option key={r}>{r}</option>)}</select></label>
          <label>Priority<select value={sample.priority} onChange={e => set({ priority: e.target.value })}>{['low', 'medium', 'high', 'critical'].map(r => <option key={r}>{r}</option>)}</select></label>
          <label className="cf-mini pep"><input type="checkbox" checked={sample.pep} onChange={e => set({ pep: e.target.checked })} />Politically exposed</label>
        </div>
        <div className="toolbar">
          {steps === null
            ? <button className="primary" onClick={() => run([])}><Play size={14} />Open a sample case</button>
            : <>
              <button onClick={() => run(steps.slice(0, -1))} disabled={!steps.length || busy}><Undo2 size={14} />Step back</button>
              <button onClick={() => run([])} disabled={busy}><RotateCcw size={14} />Start again</button>
              <button className="ghost" onClick={reset}>End trial</button>
            </>}
        </div>
        <p className="cf-help">Runs on the real case core with this client's people, then is thrown away. Nothing is saved or sent.</p>
      </div>
      {stale && <div className="cf-verdict info"><RotateCcw size={16} /><div><b>The workflow changed since this run</b>
        <p><button className="ghost" onClick={() => run(steps ?? [])}>Replay the same steps</button></p></div></div>}
      {error && <p className="cf-error" role="alert">{error}</p>}
      {result && !result.ready && <div className="cf-verdict bad"><CircleAlert size={16} /><div><b>Not ready to try</b><p>{result.why}</p></div></div>}
      {result?.ready && result.case && (
        <div className="cf-trial-run" aria-live="polite">
          <div className={`cf-verdict ${result.case.closed ? 'good' : result.stopped ? 'bad' : 'info'}`}>
            {result.case.closed ? <CheckCircle2 size={16} /> : result.stopped ? <CircleAlert size={16} /> : <Play size={16} />}
            <div>
              <b>{result.case.closed ? `Closed: ${result.case.stage_label}` : result.stopped ? 'Stopped' : `In ${result.case.stage_label}`}</b>
              <p>{result.stopped ? result.stopped.why : result.case.closed ? `Disposition ${result.case.disposition}. Priority ${result.case.priority}.`
                : `${result.case.queue_label || 'No queue'} · priority ${result.case.priority} · ${fmtMoney(Number(result.facts?.alerted_amount ?? 0))} involved`}</p>
            </div>
          </div>
          {!result.case.closed && !result.stopped && (
            <div className="cf-options">
              <h4>What can happen next</h4>
              {result.options?.length === 0 && <p className="cf-help">No step applies to this case here. Check the conditions on the steps out of this stage.</p>}
              {result.options?.map(o => (
                <button key={`${o.to}:${o.label}`} className="cf-option" disabled={busy || !o.who.length} onClick={() => run([...(steps ?? []), o.to])}>
                  <b>{o.label}</b><span>to {o.to_label}</span>
                  <small>{o.who.length ? o.who.join(', ') : 'Nobody can take it'}{o.four_eyes ? ' · four-eyes' : ''}{o.requires.length ? ` · needs ${o.requires.join(', ').replaceAll('_', ' ')}` : ''}</small>
                </button>
              ))}
            </div>
          )}
          <h4>What happened</h4>
          <ol className="cf-timeline">
            {result.steps?.map((s, i) => s.ok && s.prepared?.length ? <li key={`p${i}`} className="prep">Before “{s.label}”: {s.prepared.join(', ')}</li> : null)}
            {result.timeline?.map((e, i) => <li key={i} className={e.actor_kind}><span>{e.text}</span><em>{e.actor}</em></li>)}
          </ol>
        </div>
      )}
      {result?.ready && result.routes && (
        <div className="cf-routes">
          <h4>Every route for this sample</h4>
          <p className="cf-help">{result.routes.routes.length}{result.routes.truncated ? '+' : ''} route{result.routes.routes.length === 1 ? '' : 's'} to {result.routes.outcomes_reached.join(', ') || 'no outcome'}.
            {result.routes.outcomes_not_reached.length > 0 && ` Never reaches: ${result.routes.outcomes_not_reached.join(', ')}.`}</p>
          <ul>{result.routes.routes.slice(0, 8).map((r, i) => <li key={i}>{r.steps.map(s => s.label).join(' → ')}</li>)}</ul>
        </div>
      )}
    </div>
  )
}
