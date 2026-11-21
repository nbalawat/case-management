/** Decision tables as the API returns them (GET /api/decisions/:id) and as the editor sends them back. */
export type Kind = 'number' | 'text' | 'bool'
export type Value = string | number | boolean | null
export type InputCol = { name: string; label: string; kind: Kind; values: Value[] }
export type OutputCol = InputCol & { default: Value }
export type Rule = { id: string; when: Record<string, string>; then: Record<string, Value>; note: string }

export type Table = {
  id: string; title: string; version: string; description: string; context: string; binding: string; enabled: boolean
  hit_policy: string; aggregation: string | null; case_types: string[]
  inputs: InputCol[]; outputs: OutputCol[]; rules: Rule[]
}

export type Analysis = {
  checked: boolean; combinations: number; errors: string[]; warnings: string[]; note: string
  never_match: string[]; never_decide: string[]; gap_count: number
}
export type Version = { version: string; origin: string; published_by: string | null; published: string | null; note: string; enabled: boolean; active?: boolean }
export type Detail = Table & { yaml: string; versions: Version[]; analysis: Analysis }

export type Summary = {
  id: string; title: string; version: string; description: string; context: string; binding: string; binding_label: string
  enabled: boolean; hit_policy: string; rules: number; inputs: number; outputs: string[]
}

export type Fact = { name: string; label: string; kind: Kind; values: Value[] }
export type Reference = {
  hit_policies: string[]; aggregations: string[]; kinds: Kind[]; contexts: Record<string, Fact[]>
  bindings: { id: string; label: string; context: string; output: string; values: string[] }[]
}

export type Simulation = {
  cases: number; by_rule: Record<string, number>; outcomes: Record<string, number>; errors: number; changed: number
  compared_with: string | null; examples: { case_id: string; case_type: string; actual: string | null; would_be: Value; rules: string[] }[]
}
export type Check = {
  ok: boolean; error?: string; publishable?: boolean; is_new?: boolean; changes?: string[]
  analysis?: Analysis; simulation?: Simulation | null; table?: Table; yaml?: string
}

export const HIT_HELP: Record<string, string> = {
  UNIQUE: 'At most one rule may match. Overlaps are errors.',
  FIRST: 'The first matching rule, top to bottom, decides.',
  PRIORITY: 'Of the matching rules, the one whose answer comes first in the output\'s list of values decides.',
  ANY: 'Several rules may match, but they must give the same answer.',
  COLLECT: 'Every matching rule counts; with an aggregation their answers are added up, or the smallest, largest or count is taken.',
  'RULE ORDER': 'Every matching rule, in order, as a list.',
}

/** Display a cell value as the editor shows it. */
export const show = (v: Value) => (v === null || v === undefined ? '' : typeof v === 'boolean' ? String(v) : String(v))

/** Read what someone typed into an output cell back into a value of the column's kind. */
export function parseValue(raw: string, kind: Kind): Value {
  const t = raw.trim()
  if (t === '' || t === 'null') return null
  if (kind === 'number') { const n = Number(t); return Number.isFinite(n) ? n : t }
  if (kind === 'bool') return t === 'true' ? true : t === 'false' ? false : t
  return t.replace(/^"(.*)"$/, '$1')
}

/** The draft in the shape POST /api/decisions/check expects. */
export const toDraft = (t: Table) => ({
  id: t.id, title: t.title, version: t.version, description: t.description, context: t.context, binding: t.binding, enabled: t.enabled,
  hit_policy: t.hit_policy, aggregation: t.hit_policy === 'COLLECT' ? t.aggregation : null, case_types: t.case_types,
  inputs: t.inputs, outputs: t.outputs, rules: t.rules,
})

export function nextVersion(v: string) {
  const parts = v.split('.').map(Number)
  parts[parts.length - 1] += 1
  return parts.join('.')
}
