/** Forms as the API returns them (GET /api/forms/:id) and as the builder sends them back. */
export type FieldType = 'text' | 'long_text' | 'number' | 'money' | 'date' | 'email' | 'boolean' | 'choice' | 'multi_choice'
export type Answer = string | number | boolean | string[] | null | undefined
export type Answers = Record<string, Answer>

export type Field = {
  key: string; label: string; type: FieldType; required: boolean; help: string
  options: string[]; option_labels: Record<string, string>
  minimum: number | null; maximum: number | null; min_length: number | null; max_length: number | null; pattern: string | null
  visible_when: string | null; prefill: string | null
}
export type Section = { title: string; fields: Field[] }
export type Form = { id: string; title: string; version: string; description: string; case_types: string[]; stages: string[]; sections: Section[] }
export type FormDetail = Form & { yaml: string; json_schema: object; versions: { version: string; origin: string; published_by: string | null; published: string | null; note: string; active?: boolean }[] }
export type FormSummary = { id: string; title: string; version: string; description: string; case_types: string[]; stages: string[]; fields: number; sections: number }
export type FormReference = {
  types: { id: FieldType; label: string }[]
  prefill: { name: string; label: string; kind: string }[]
  case_types: { id: string; title: string; stages: { id: string; label: string }[] }[]
}
export type FormCheck = { ok: boolean; error?: string; changes?: string[]; json_schema?: object; is_new?: boolean }

/** One form on a case (GET /api/cases/:id/forms). */
export type CaseForm = {
  form: Form; status: 'not_started' | 'draft' | 'complete'; data: Answers; missing: Record<string, string>
  updated_by: string | null; updated: string | null; completed_by: string | null; completed_at: string | null; locked: string | null
}

const WHEN = /^\s*([a-z][a-z0-9_]*)\s*(==|!=)\s*(.+?)\s*$/

function literal(raw: string): Answer {
  const t = raw.replace(/^["']|["']$/g, '')
  if (t === 'true' || t === 'false') return t === 'true'
  const n = Number(t)
  return t !== '' && Number.isFinite(n) ? n : t
}

/** The same visibility rule the server applies: a field shows when its condition on an earlier answer holds. */
export function visible(field: Field, answers: Answers): boolean {
  if (!field.visible_when) return true
  const m = WHEN.exec(field.visible_when)
  if (!m) return true
  const [, key, op, raw] = m
  const want = literal(raw)
  const have = answers[key]
  const same = have === want || (Array.isArray(have) && have.includes(String(want)))
  return op === '==' ? same : !same
}

export const optionLabel = (f: Field, o: string) => f.option_labels[o] ?? o.replaceAll('_', ' ')

export const blankField = (n: number, type: FieldType = 'text'): Field => ({
  key: `field_${n}`, label: `Question ${n}`, type, required: false, help: '',
  options: type === 'choice' || type === 'multi_choice' ? ['option_a', 'option_b'] : [], option_labels: {},
  minimum: null, maximum: null, min_length: null, max_length: null, pattern: null, visible_when: null, prefill: null,
})

export function nextVersion(v: string) {
  const parts = v.split('.').map(Number)
  parts[parts.length - 1] += 1
  return parts.join('.')
}
