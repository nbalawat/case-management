import { useState } from 'react'
import { api } from '../../api'
import { Card, Pill } from '../ui'
import { parseValue, show, toDraft, type Table, type Value } from './types'

type Answer = { ok: boolean; error?: string; outputs?: Record<string, Value | Value[]>; matched?: string[]; defaulted?: boolean }

/** Try the draft on screen against inputs typed by hand. Nothing is stored. */
export default function TryPanel({ table, onMatched }: { table: Table; onMatched: (ids: string[]) => void }) {
  const [inputs, setInputs] = useState<Record<string, string>>({})
  const [answer, setAnswer] = useState<Answer | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const run = () => {
    const body = Object.fromEntries(table.inputs.filter(c => inputs[c.name]?.trim()).map(c => [c.name, parseValue(inputs[c.name], c.kind)]))
    api.post<Answer>(`/decisions/${table.id}/evaluate`, { inputs: body, table: toDraft(table) })
      .then(a => { setAnswer(a); setErr(null); onMatched(a.matched ?? []) })
      .catch(e => { setErr(e.message); setAnswer(null); onMatched([]) })
  }
  return (
    <Card title="Try it" action={<span className="muted small">Runs the table as it is on screen</span>}>
      <div className="dt-try">
        {table.inputs.map(c => (
          <label key={c.name}>
            <span>{c.label}</span>
            {c.kind === 'bool' || c.values.length ? (
              <select value={inputs[c.name] ?? ''} onChange={e => setInputs({ ...inputs, [c.name]: e.target.value })}>
                <option value="">(not given)</option>
                {(c.kind === 'bool' ? [true, false] : c.values).map(v => <option key={show(v)} value={show(v)}>{show(v)}</option>)}
              </select>
            ) : (
              <input type={c.kind === 'number' ? 'number' : 'text'} step="any" value={inputs[c.name] ?? ''} onChange={e => setInputs({ ...inputs, [c.name]: e.target.value })} />
            )}
          </label>
        ))}
      </div>
      <div className="toolbar"><button className="primary" onClick={run}>Evaluate</button></div>
      {err && <p className="msg" role="alert">{err}</p>}
      {answer && !answer.ok && <div className="verdict bad"><b>The hit policy was broken</b><p>{answer.error}</p></div>}
      {answer?.ok && (
        <div className="verdict good" role="status">
          {Object.entries(answer.outputs ?? {}).map(([k, v]) => <div key={k}><b>{k}</b>: {Array.isArray(v) ? v.map(show).join(', ') || '(none)' : show(v) || '(no answer)'}</div>)}
          <div className="small">
            {answer.defaulted ? 'No rule matched: these are the defaults.' : <>Decided by {answer.matched!.map(m => <Pill key={m}>{m}</Pill>)}</>}
          </div>
        </div>
      )}
    </Card>
  )
}
