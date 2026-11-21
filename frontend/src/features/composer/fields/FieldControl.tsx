import { useId, useState } from 'react'
import { Check, Plus, Trash2, X } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { OPS } from '../api'
import { optionsFor } from './options'
import type { Catalog, Cond, CondGroup, FieldSpec, Option } from '../types'


export function Field({ label, help, required, children, htmlFor }: { label: string; help?: string; required?: boolean; children: React.ReactNode; htmlFor?: string }) {
  return (
    <div className="cf-field">
      <label htmlFor={htmlFor}>{label}{required && <span className="req" aria-hidden> *</span>}</label>
      {children}
      {help && <p className="cf-help">{help}</p>}
    </div>
  )
}

export function MultiSelect({ options, value, onChange, disabled, label }: { options: Option[]; value: string[]; onChange: (v: string[]) => void; disabled?: boolean; label: string }) {
  const [q, setQ] = useState('')
  const shown = options.filter(o => !q || `${o.label} ${o.id}`.toLowerCase().includes(q.toLowerCase()))
  const toggle = (id: string) => onChange(value.includes(id) ? value.filter(v => v !== id) : [...value, id])
  const unknown = value.filter(v => !options.some(o => o.id === v))
  return (
    <div className="cf-multi" role="group" aria-label={label}>
      {options.length > 10 && <input type="search" placeholder="Filter" value={q} onChange={e => setQ(e.target.value)} aria-label={`Filter ${label}`} />}
      <div className="cf-chips">
        {shown.map(o => {
          const on = value.includes(o.id)
          const people = (o as Option & { people?: string[] }).people
          return (
            <button key={o.id} type="button" className={cn('cf-toggle', on && 'on')} aria-pressed={on} disabled={disabled} onClick={() => toggle(o.id)}
              title={people?.length ? `Held by ${people.join(', ')}` : people ? 'Nobody holds this role' : undefined}>
              {on && <Check size={12} />}{o.label}
            </button>
          )
        })}
        {unknown.map(v => <button key={v} type="button" className="cf-toggle on bad" onClick={() => toggle(v)} title="Not known at this client"><X size={12} />{v}</button>)}
      </div>
    </div>
  )
}

/** One setting drawn from its description. */
export function FieldControl({ cat, spec, value, onChange, disabled }: { cat: Catalog; spec: FieldSpec; value: unknown; onChange: (v: unknown) => void; disabled?: boolean }) {
  const id = useId()
  const opts = optionsFor(cat, spec)
  let control: React.ReactNode
  switch (spec.kind) {
    case 'textarea':
      control = <textarea id={id} rows={3} value={String(value ?? '')} disabled={disabled} onChange={e => onChange(e.target.value)} />
      break
    case 'number':
      control = <input id={id} type="number" min={0} value={value === null || value === undefined ? '' : String(value)} disabled={disabled}
        onChange={e => onChange(e.target.value === '' ? null : Number(e.target.value))} />
      break
    case 'bool':
      control = <label className="cf-switch"><input id={id} type="checkbox" checked={!!value} disabled={disabled} onChange={e => onChange(e.target.checked)} /><span>{value ? 'On' : 'Off'}</span></label>
      break
    case 'multiselect':
      control = <MultiSelect label={spec.label} options={opts} value={(value as string[]) ?? []} onChange={onChange} disabled={disabled} />
      break
    case 'select':
      if (spec.options === 'dispositions') {
        control = <>
          <input id={id} list={`${id}-l`} value={String(value ?? '')} disabled={disabled} onChange={e => onChange(e.target.value.trim().toLowerCase().replace(/[^a-z0-9_]+/g, '_'))} />
          <datalist id={`${id}-l`}>{opts.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</datalist>
        </>
      } else {
        control = <select id={id} value={String(value ?? '')} disabled={disabled} onChange={e => onChange(e.target.value || null)}>
          {!spec.required && <option value="">None</option>}
          {spec.required && !value && <option value="">Choose…</option>}
          {opts.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
      }
      break
    default:
      control = <input id={id} type="text" value={String(value ?? '')} disabled={disabled} onChange={e => onChange(e.target.value)} />
  }
  const help = spec.options === 'dispositions' ? (cat.reference.dispositions.find(d => d.id === value)?.does || spec.help) : spec.help
  return <Field label={spec.label} help={help} required={spec.required} htmlFor={id}>{control}</Field>
}

/** Conditions as sentences: "Total value of alerted transactions is at least 100000". */
export function ConditionBuilder({ cat, group, onChange, disabled, empty = 'Always' }: { cat: Catalog; group: CondGroup | undefined; onChange: (g: CondGroup) => void; disabled?: boolean; empty?: string }) {
  const g: CondGroup = group ?? { match: 'all', conditions: [] }
  const facts = cat.reference.facts
  const set = (i: number, patch: Partial<Cond>) => onChange({ ...g, conditions: g.conditions.map((c, j) => (j === i ? { ...c, ...patch } : c)) })
  const add = () => {
    const f = facts[0]
    onChange({ ...g, conditions: [...g.conditions, { fact: f.id, op: f.kind === 'number' ? '>=' : '==', value: f.kind === 'bool' ? true : f.values[0] ?? (f.kind === 'number' ? 0 : '') }] })
  }
  if (g.raw) return <p className="cf-help">Written by hand: <code>{JSON.stringify(g.raw)}</code>. Edit it in the case type YAML.</p>
  return (
    <div className="cf-conds">
      {g.conditions.length > 1 && (
        <div className="seg cf-match" role="group" aria-label="Match">
          {(['all', 'any'] as const).map(m => <button key={m} type="button" disabled={disabled} aria-pressed={(g.match ?? 'all') === m} onClick={() => onChange({ ...g, match: m })}>{m === 'all' ? 'All of these' : 'Any of these'}</button>)}
        </div>
      )}
      {g.conditions.length === 0 && <p className="cf-help">{empty}</p>}
      {g.conditions.map((c, i) => {
        const fact = facts.find(f => f.id === c.fact)
        const ops = OPS.filter(o => o.kinds.includes(fact?.kind ?? 'text'))
        return (
          <div key={i} className="cf-cond">
            <select aria-label="Fact" value={c.fact} disabled={disabled} onChange={e => {
              const nf = facts.find(f => f.id === e.target.value)!
              set(i, { fact: nf.id, op: nf.kind === 'number' ? '>=' : '==', value: nf.kind === 'bool' ? true : nf.values[0] ?? (nf.kind === 'number' ? 0 : '') })
            }}>{facts.map(f => <option key={f.id} value={f.id}>{f.label}</option>)}</select>
            <select aria-label="Comparison" value={c.op} disabled={disabled} onChange={e => set(i, { op: e.target.value })}>{ops.map(o => <option key={o.id} value={o.id}>{o.label}</option>)}</select>
            {fact?.kind === 'bool' ? (
              <select aria-label="Value" value={String(c.value)} disabled={disabled} onChange={e => set(i, { value: e.target.value === 'true' })}><option value="true">yes</option><option value="false">no</option></select>
            ) : fact?.kind === 'choice' ? (
              <select aria-label="Value" value={String(c.value)} disabled={disabled} onChange={e => set(i, { value: e.target.value })}>{fact.values.map(v => <option key={v} value={v}>{v}</option>)}</select>
            ) : (
              <input aria-label="Value" type={fact?.kind === 'number' ? 'number' : 'text'} value={String(c.value)} disabled={disabled}
                onChange={e => set(i, { value: fact?.kind === 'number' ? Number(e.target.value) : e.target.value })} />
            )}
            <button type="button" className="ghost cf-icon-btn" aria-label="Remove condition" disabled={disabled} onClick={() => onChange({ ...g, conditions: g.conditions.filter((_, j) => j !== i) })}><Trash2 size={14} /></button>
          </div>
        )
      })}
      <button type="button" className="ghost cf-add" disabled={disabled} onClick={add}><Plus size={14} />Add a condition</button>
    </div>
  )
}
