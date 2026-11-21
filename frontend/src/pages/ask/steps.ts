import { words } from '../../api'
import { objectWords } from './format'
import type { Step } from './types'

const TOOL: Record<string, (s: Step) => string> = {
  breakdown: s => `Counted and compared ${objectWords(s.input.object)}`,
  list_cases: () => 'Listed matching cases',
  list_records: s => `Listed matching ${objectWords(s.input.object)}`,
  record: s => `Read ${s.input.object && s.input.object !== 'cases' ? `one of the ${words(s.input.object)}` : 'a case'}`,
  case_detail: () => 'Read a case', customer: () => 'Looked up a customer', team_workload: () => 'Read team workload',
  calculate: () => 'Worked out a figure',
  policy_search: () => 'Searched the knowledge library',
}
export const toolLabel = (s: Step) => (TOOL[s.tool] ?? (() => words(s.tool)))(s)

/** The lookup worth showing beside the answer: the last one that produced a table. */
export const mainStep = (steps: Step[]) =>
  [...steps].reverse().find(s => s.ok && ['breakdown', 'list_cases', 'list_records', 'team_workload'].includes(s.tool))
