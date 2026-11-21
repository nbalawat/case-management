import { ArrowDown, ArrowUp, Plus, Trash2, X } from 'lucide-react'
import { Pill } from '../ui'
import { parseValue, show, type Fact, type Table } from './types'

type Props = {
  table: Table
  facts: Fact[]
  readOnly: boolean
  /** Rule ids to flag: overlapping, never deciding, or matched by the last try. */
  flagged: Record<string, string>
  onChange: (t: Table) => void
}

/**
 * The table as DMN tools draw it: one row per rule, input columns then output columns, each cell a FEEL test or value.
 * Every edit produces a new table object; nothing is kept in local state, so a check always sees what is on screen.
 */
export default function DecisionGrid({ table: t, facts, readOnly, flagged, onChange }: Props) {
  const setRule = (i: number, patch: Partial<Table['rules'][number]>) =>
    onChange({ ...t, rules: t.rules.map((r, j) => (j === i ? { ...r, ...patch } : r)) })
  const move = (i: number, by: number) => {
    const rules = [...t.rules]
    const [r] = rules.splice(i, 1)
    rules.splice(i + by, 0, r)
    onChange({ ...t, rules })
  }
  const addRule = () => {
    const n = t.rules.length + 1
    let id = `r${n}`
    for (let k = n; t.rules.some(r => r.id === id); k++) id = `r${k + 1}`
    onChange({ ...t, rules: [...t.rules, { id, when: {}, then: Object.fromEntries(t.outputs.map(o => [o.name, o.default ?? (o.values[0] ?? null)])), note: '' }] })
  }
  const unused = facts.filter(f => !t.inputs.some(c => c.name === f.name))
  const addInput = (name: string) => {
    const f = facts.find(x => x.name === name)
    if (f) onChange({ ...t, inputs: [...t.inputs, { name: f.name, label: f.label, kind: f.kind, values: f.values }] })
  }
  const dropInput = (name: string) =>
    onChange({ ...t, inputs: t.inputs.filter(c => c.name !== name), rules: t.rules.map(r => ({ ...r, when: Object.fromEntries(Object.entries(r.when).filter(([k]) => k !== name)) })) })

  return (
    <div className="dt-wrap">
      <table className="dt-grid">
        <thead>
          <tr>
            <th className="dt-hit" title={t.hit_policy}>{t.hit_policy === 'RULE ORDER' ? 'R' : t.hit_policy[0]}{t.aggregation && t.hit_policy === 'COLLECT' ? ` ${t.aggregation === 'COUNT' ? '#' : t.aggregation === 'SUM' ? '+' : t.aggregation === 'MIN' ? '<' : '>'}` : ''}</th>
            {t.inputs.map(c => (
              <th key={c.name} className="dt-in" scope="col">
                <span>{c.label}</span><small>{c.name} · {c.kind}</small>
                {!readOnly && <button className="dt-x" aria-label={`Remove input ${c.label}`} onClick={() => dropInput(c.name)}><X size={12} /></button>}
              </th>
            ))}
            {!readOnly && unused.length > 0 && (
              <th className="dt-in dt-add">
                <select aria-label="Add an input" value="" onChange={e => addInput(e.target.value)}>
                  <option value="">+ input</option>
                  {unused.map(f => <option key={f.name} value={f.name}>{f.label}</option>)}
                </select>
              </th>
            )}
            {t.outputs.map(c => (
              <th key={c.name} className="dt-out" scope="col"><span>{c.label}</span><small>{c.name}{c.values.length ? `: ${c.values.map(show).join(', ')}` : ` · ${c.kind}`}</small></th>
            ))}
            <th className="dt-note" scope="col">Note</th>
            {!readOnly && <th aria-label="Row actions" />}
          </tr>
        </thead>
        <tbody>
          {t.rules.map((r, i) => (
            <tr key={r.id} className={flagged[r.id] ? `dt-flag ${flagged[r.id]}` : ''}>
              <th scope="row" className="dt-id" title={flagged[r.id] === 'hit' ? 'Matched by the last try' : flagged[r.id] ? 'See the check results' : ''}>{i + 1}<small>{r.id}</small></th>
              {t.inputs.map(c => (
                <td key={c.name} className="dt-in">
                  <input aria-label={`Rule ${i + 1}, ${c.label}`} value={r.when[c.name] ?? ''} placeholder="-" readOnly={readOnly} spellCheck={false}
                    onChange={e => setRule(i, { when: { ...r.when, [c.name]: e.target.value } })} />
                </td>
              ))}
              {!readOnly && unused.length > 0 && <td className="dt-in dt-add" />}
              {t.outputs.map(c => (
                <td key={c.name} className="dt-out">
                  {c.values.length ? (
                    <select aria-label={`Rule ${i + 1}, ${c.label}`} disabled={readOnly} value={show(r.then[c.name])}
                      onChange={e => setRule(i, { then: { ...r.then, [c.name]: parseValue(e.target.value, c.kind) } })}>
                      {c.values.map(v => <option key={show(v)} value={show(v)}>{show(v)}</option>)}
                    </select>
                  ) : (
                    <input aria-label={`Rule ${i + 1}, ${c.label}`} value={show(r.then[c.name])} readOnly={readOnly} spellCheck={false}
                      onChange={e => setRule(i, { then: { ...r.then, [c.name]: parseValue(e.target.value, c.kind) } })} />
                  )}
                </td>
              ))}
              <td className="dt-note"><input aria-label={`Rule ${i + 1}, note`} value={r.note} readOnly={readOnly} onChange={e => setRule(i, { note: e.target.value })} /></td>
              {!readOnly && (
                <td className="dt-act">
                  <button aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}><ArrowUp size={14} /></button>
                  <button aria-label="Move down" disabled={i === t.rules.length - 1} onClick={() => move(i, 1)}><ArrowDown size={14} /></button>
                  <button aria-label="Delete rule" onClick={() => onChange({ ...t, rules: t.rules.filter((_, j) => j !== i) })}><Trash2 size={14} /></button>
                </td>
              )}
            </tr>
          ))}
          <tr className="dt-default">
            <th scope="row" className="dt-id">else</th>
            <td colSpan={t.inputs.length + (!readOnly && unused.length > 0 ? 1 : 0)} className="muted small">When no rule matches</td>
            {t.outputs.map(c => <td key={c.name} className="dt-out">{c.default === null || c.default === undefined ? <Pill tone="warning">no answer</Pill> : <b>{show(c.default)}</b>}</td>)}
            <td colSpan={readOnly ? 1 : 2} />
          </tr>
        </tbody>
      </table>
      {!readOnly && <button className="dt-addrule" onClick={addRule}><Plus size={14} /> Add a rule</button>}
      <p className="muted small dt-help">
        Cells are FEEL tests: <code>-</code> anything, <code>{'>= 100000'}</code>, <code>[0.6..0.9)</code>, <code>high, critical</code>,{' '}
        <code>not(low)</code>, <code>true</code>. Text needs no quotes.
      </p>
    </div>
  )
}
