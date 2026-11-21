import { optionLabel, visible, type Answer, type Answers, type Field, type Form } from './types'

type Props = {
  form: Form
  answers: Answers
  /** Field key -> what is wrong, shown under the field. */
  problems?: Record<string, string>
  readOnly?: boolean
  onChange?: (a: Answers) => void
}

function Input({ f, value, readOnly, set }: { f: Field; value: Answer; readOnly: boolean; set: (v: Answer) => void }) {
  const id = `fld-${f.key}`
  switch (f.type) {
    case 'long_text':
      return <textarea id={id} rows={4} value={(value as string) ?? ''} readOnly={readOnly} maxLength={f.max_length ?? undefined} onChange={e => set(e.target.value)} />
    case 'number':
    case 'money':
      return (
        <span className={f.type === 'money' ? 'fm-money' : ''}>
          <input id={id} type="number" step="any" value={value === null || value === undefined ? '' : String(value)} readOnly={readOnly}
            min={f.minimum ?? undefined} max={f.maximum ?? undefined} onChange={e => set(e.target.value === '' ? null : Number(e.target.value))} />
        </span>
      )
    case 'date':
      return <input id={id} type="date" value={(value as string) ?? ''} readOnly={readOnly} onChange={e => set(e.target.value || null)} />
    case 'email':
      return <input id={id} type="email" value={(value as string) ?? ''} readOnly={readOnly} onChange={e => set(e.target.value)} />
    case 'boolean':
      return (
        <span className="fm-seg" role="radiogroup" aria-labelledby={`${id}-l`}>
          {[true, false].map(b => (
            <button type="button" key={String(b)} role="radio" aria-checked={value === b} disabled={readOnly} onClick={() => set(value === b ? null : b)}>{b ? 'Yes' : 'No'}</button>
          ))}
        </span>
      )
    case 'choice':
      return f.options.length <= 4 ? (
        <span className="fm-seg" role="radiogroup" aria-labelledby={`${id}-l`}>
          {f.options.map(o => <button type="button" key={o} role="radio" aria-checked={value === o} disabled={readOnly} onClick={() => set(value === o ? null : o)}>{optionLabel(f, o)}</button>)}
        </span>
      ) : (
        <select id={id} value={(value as string) ?? ''} disabled={readOnly} onChange={e => set(e.target.value || null)}>
          <option value="">Choose…</option>
          {f.options.map(o => <option key={o} value={o}>{optionLabel(f, o)}</option>)}
        </select>
      )
    case 'multi_choice': {
      const have = Array.isArray(value) ? value : []
      return (
        <span className="fm-checks">
          {f.options.map(o => (
            <label key={o}><input type="checkbox" checked={have.includes(o)} disabled={readOnly}
              onChange={e => set(e.target.checked ? [...have, o] : have.filter(x => x !== o))} />{optionLabel(f, o)}</label>
          ))}
        </span>
      )
    }
    default:
      return <input id={id} type="text" value={(value as string) ?? ''} readOnly={readOnly} maxLength={f.max_length ?? undefined} onChange={e => set(e.target.value)} />
  }
}

/** Draws any form definition. Fields hidden by an earlier answer are not shown; the server drops their answers too. */
export default function FormRenderer({ form, answers, problems = {}, readOnly = false, onChange }: Props) {
  const set = (key: string, v: Answer) => onChange?.({ ...answers, [key]: v })
  return (
    <div className="fm">
      {form.sections.map(s => (
        <fieldset key={s.title} className="fm-section">
          <legend>{s.title}</legend>
          {s.fields.filter(f => visible(f, answers)).map(f => (
            <div key={f.key} className={`fm-field${problems[f.key] ? ' bad' : ''}`}>
              <label id={`fld-${f.key}-l`} htmlFor={`fld-${f.key}`}>{f.label}{f.required && <span className="fm-req" aria-label="required">*</span>}</label>
              {f.help && <p className="fm-help">{f.help}</p>}
              <Input f={f} value={answers[f.key]} readOnly={readOnly} set={v => set(f.key, v)} />
              {problems[f.key] && <p className="fm-err" role="alert">{problems[f.key]}</p>}
            </div>
          ))}
        </fieldset>
      ))}
    </div>
  )
}
