import { ArrowDown, ArrowUp, Plus, Trash2 } from 'lucide-react'
import { blankField, optionLabel, type Field, type FieldType, type Form, type FormReference } from './types'

type Sel = { s: number; f: number }
type Props = { form: Form; reference: FormReference; selected: Sel | null; onSelect: (s: Sel | null) => void; onChange: (f: Form) => void; readOnly: boolean }

const num = (v: string) => (v.trim() === '' ? null : Number(v))

/** Field properties: everything a field can say about itself, edited in place. */
function Properties({ field, before, reference, onChange }: { field: Field; before: Field[]; reference: FormReference; onChange: (f: Field) => void }) {
  const set = (patch: Partial<Field>) => onChange({ ...field, ...patch })
  const isChoice = field.type === 'choice' || field.type === 'multi_choice'
  const isNum = field.type === 'number' || field.type === 'money'
  const isText = field.type === 'text' || field.type === 'long_text'
  const [whenKey, whenOp, whenVal] = (field.visible_when ?? '').split(/\s*(==|!=)\s*/)
  const target = before.find(f => f.key === whenKey)
  const setWhen = (k: string, op: string, v: string) => set({ visible_when: k ? `${k} ${op || '=='} ${v}` : null })
  return (
    <div className="fb-props">
      <label><span>Question</span><input value={field.label} onChange={e => set({ label: e.target.value })} /></label>
      <label><span>Key</span><input value={field.key} spellCheck={false} onChange={e => set({ key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, '_') })} /></label>
      <label><span>Answer type</span>
        <select value={field.type} onChange={e => {
          const type = e.target.value as FieldType
          const choice = type === 'choice' || type === 'multi_choice'
          set({ type, options: choice ? (field.options.length ? field.options : ['option_a', 'option_b']) : [], option_labels: choice ? field.option_labels : {},
            minimum: null, maximum: null, min_length: null, max_length: null })
        }}>{reference.types.map(t => <option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
      <label className="fb-check"><input type="checkbox" checked={field.required} onChange={e => set({ required: e.target.checked })} /> Required</label>
      <label className="fb-wide"><span>Help text</span><input value={field.help} onChange={e => set({ help: e.target.value })} /></label>
      {isChoice && (
        <label className="fb-wide"><span>Options, one per line as key or key: label</span>
          <textarea rows={Math.max(3, field.options.length)} value={field.options.map(o => (field.option_labels[o] ? `${o}: ${field.option_labels[o]}` : o)).join('\n')}
            onChange={e => {
              const lines = e.target.value.split('\n').map(l => l.trim()).filter(Boolean)
              const options = lines.map(l => l.split(':')[0].trim().toLowerCase().replace(/[^a-z0-9_]/g, '_'))
              const labels = Object.fromEntries(lines.filter(l => l.includes(':')).map(l => [l.split(':')[0].trim().toLowerCase().replace(/[^a-z0-9_]/g, '_'), l.slice(l.indexOf(':') + 1).trim()]))
              set({ options, option_labels: labels })
            }} /></label>
      )}
      {isNum && <>
        <label><span>Minimum</span><input type="number" value={field.minimum ?? ''} onChange={e => set({ minimum: num(e.target.value) })} /></label>
        <label><span>Maximum</span><input type="number" value={field.maximum ?? ''} onChange={e => set({ maximum: num(e.target.value) })} /></label>
      </>}
      {isText && <>
        <label><span>Shortest answer</span><input type="number" min={0} value={field.min_length ?? ''} onChange={e => set({ min_length: num(e.target.value) })} /></label>
        <label><span>Longest answer</span><input type="number" min={0} value={field.max_length ?? ''} onChange={e => set({ max_length: num(e.target.value) })} /></label>
      </>}
      <label className="fb-wide"><span>Show only when</span>
        <span className="fb-when">
          <select aria-label="Earlier question" value={whenKey ?? ''} onChange={e => setWhen(e.target.value, whenOp, '')}>
            <option value="">always shown</option>
            {before.filter(f => f.type === 'choice' || f.type === 'multi_choice' || f.type === 'boolean').map(f => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
          {target && <>
            <select aria-label="Comparison" value={whenOp} onChange={e => setWhen(whenKey, e.target.value, whenVal ?? '')}><option value="==">is</option><option value="!=">is not</option></select>
            <select aria-label="Answer" value={whenVal ?? ''} onChange={e => setWhen(whenKey, whenOp, e.target.value)}>
              <option value="">choose…</option>
              {(target.type === 'boolean' ? ['true', 'false'] : target.options).map(o => <option key={o} value={o}>{target.type === 'boolean' ? (o === 'true' ? 'Yes' : 'No') : optionLabel(target, o)}</option>)}
            </select>
          </>}
        </span></label>
      <label className="fb-wide"><span>Start with the case's</span>
        <select value={field.prefill ?? ''} onChange={e => set({ prefill: e.target.value || null })}>
          <option value="">nothing</option>
          {reference.prefill.map(p => <option key={p.name} value={p.name}>{p.label}</option>)}
        </select></label>
    </div>
  )
}

/** The form's outline: sections and their questions, with the selected question's properties beside it. */
export default function FormBuilder({ form, reference, selected, onSelect, onChange, readOnly }: Props) {
  const sections = form.sections
  const total = sections.reduce((n, s) => n + s.fields.length, 0)
  const setSection = (i: number, patch: Partial<Form['sections'][number]>) => onChange({ ...form, sections: sections.map((s, j) => (j === i ? { ...s, ...patch } : s)) })
  const setField = (si: number, fi: number, f: Field) => setSection(si, { fields: sections[si].fields.map((x, j) => (j === fi ? f : x)) })
  const moveField = (si: number, fi: number, by: number) => {
    const fields = [...sections[si].fields]
    const [f] = fields.splice(fi, 1)
    fields.splice(fi + by, 0, f)
    setSection(si, { fields })
    onSelect({ s: si, f: fi + by })
  }
  const addField = (si: number, type: FieldType) => {
    let n = total + 1
    while (sections.some(s => s.fields.some(f => f.key === `field_${n}`))) n++
    setSection(si, { fields: [...sections[si].fields, blankField(n, type)] })
    onSelect({ s: si, f: sections[si].fields.length })
  }
  const field = selected ? sections[selected.s]?.fields[selected.f] : undefined
  const before = selected ? sections.flatMap((s, si) => s.fields.filter((_, fi) => si < selected.s || (si === selected.s && fi < selected.f))) : []
  return (
    <div className="fb">
      <div className="fb-outline">
        {sections.map((s, si) => (
          <div key={si} className="fb-section">
            <div className="fb-section-h">
              <input aria-label="Section title" value={s.title} readOnly={readOnly} onChange={e => setSection(si, { title: e.target.value })} />
              {!readOnly && sections.length > 1 && <button aria-label="Remove section" onClick={() => { onChange({ ...form, sections: sections.filter((_, j) => j !== si) }); onSelect(null) }}><Trash2 size={14} /></button>}
            </div>
            <ol>
              {s.fields.map((f, fi) => (
                <li key={`${f.key}-${fi}`} aria-current={selected?.s === si && selected.f === fi}>
                  <button className="fb-item" onClick={() => onSelect({ s: si, f: fi })}>
                    <b>{f.label}{f.required && ' *'}</b><span>{reference.types.find(t => t.id === f.type)?.label}{f.visible_when ? ` · when ${f.visible_when}` : ''}{f.prefill ? ` · prefilled` : ''}</span>
                  </button>
                  {!readOnly && <span className="fb-row-act">
                    <button aria-label="Move up" disabled={fi === 0} onClick={() => moveField(si, fi, -1)}><ArrowUp size={13} /></button>
                    <button aria-label="Move down" disabled={fi === s.fields.length - 1} onClick={() => moveField(si, fi, 1)}><ArrowDown size={13} /></button>
                    <button aria-label="Delete question" onClick={() => { setSection(si, { fields: s.fields.filter((_, j) => j !== fi) }); onSelect(null) }}><Trash2 size={13} /></button>
                  </span>}
                </li>
              ))}
            </ol>
            {!readOnly && (
              <div className="fb-palette" aria-label="Add a question">
                {reference.types.map(t => <button key={t.id} onClick={() => addField(si, t.id)} title={`Add: ${t.label}`}><Plus size={12} />{t.label}</button>)}
              </div>
            )}
          </div>
        ))}
        {!readOnly && <button className="fb-addsec" onClick={() => onChange({ ...form, sections: [...sections, { title: `Section ${sections.length + 1}`, fields: [blankField(total + 1)] }] })}><Plus size={14} /> Add a section</button>}
      </div>
      <div className="fb-side">
        {field && selected && !readOnly
          ? <Properties key={`${selected.s}-${selected.f}`} field={field} before={before} reference={reference} onChange={f => setField(selected.s, selected.f, f)} />
          : <p className="empty">{readOnly ? 'Start a new version to change the form.' : 'Select a question to edit it, or add one from the palette under a section.'}</p>}
      </div>
    </div>
  )
}
