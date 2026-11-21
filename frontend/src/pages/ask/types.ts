// The shapes Ask sends back. Case results and results over other records (transactions, alerts, filings...) differ a
// little: case breakdowns name `case_ids` and `matching_cases`, the others `ids`, `matching` and the unit of each measure.

export type Filter = { field: string; op: string; value: unknown }
export type Unit = 'count' | 'usd' | 'percent' | 'days' | 'score' | string

export type BreakdownRow = { key: (string | number)[]; values: (number | null)[]; case_ids?: string[]; ids?: string[]; records?: number }
export type Breakdown = {
  object?: string; record_kind?: string; columns: string[]; group_by: string[]; measures: string[]; units?: Unit[]
  groups: number; truncated: boolean; matching_cases?: number; matching?: number; note?: string; rows: BreakdownRow[]
}
export type CaseList = { columns: string[]; cases: Record<string, string | number | null>[]; matching_cases: number; truncated: boolean }
export type RecordList = { object: string; record_kind: string; columns: string[]; records: Record<string, unknown>[]; matching: number; truncated: boolean }
export type OneRecord = { object: string; record: Record<string, unknown> }
export type Calculation = { expression: string; value: number }
export type TeamLoad = { people: { name: string; title: string; open_cases: number; past_stage_clock: number; closed_cases: number }[] }

export type StepInput = {
  object?: string; filters?: Filter[]; group_by?: string[]; measures?: string[]; sort_by?: string
  case_id?: string; name_or_id?: string; id?: string; expression?: string
}
export type Step = { tool: string; input: StepInput; ok: boolean; summary: string; result?: unknown }

export type Cited = { id: string; customer: string; case_type: string; stage: string; status: string }
export type Source = { kind: string; id: string; label: string; link: string; shown_by_tool: boolean }
export type Figures = { stated: number; verified: string[]; unverified: string[] }

export type Answer = {
  id: string; conversation_id: string; question: string; answer: string; status: string; provider: string; routed?: string
  cited: Cited[]; sources: Source[]; unverified: string[]; figures: Figures; steps: Step[]; duration_ms: number
}
export type Scope = { client: string; person: string; title: string; team_workload: boolean; tools: string[]; objects?: string[] }
export type Start = { suggestions: string[]; scope: Scope; provider: string; model?: { reason?: string } }
export type LogRow = {
  id: string; asked_by: string; asked_by_name: string; created: string; question: string; status: string; provider: string
  steps: Step[]; cited: string[]; figures?: Figures
}
