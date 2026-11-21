export type Outcome = 'productive' | 'unproductive' | 'undecided' | 'never_raised'
export type BacktestItem = { key: string; party_id: string; party_name: string; score: number; explanation: string; outcome: Outcome; case_id: string | null; disposition: string | null }
export type Backtest = {
  days: number; ran_at: string; from: Record<string, number>; to: Record<string, number>; enabled: boolean
  alerts_now: number; alerts_then: number; kept: number; productive_now: number; productive_lost: number; summary: string; caution: string
  removed: { count: number; productive: number; unproductive: number; undecided: number; never_raised: number; items: BacktestItem[] }
  added: { count: number; customers: number; items: BacktestItem[] }
}
export type Version = {
  id: string; scenario: string; number: number; params: Record<string, number>; enabled: boolean; status: string; reason: string
  proposed_by: string; proposed_at: string; decided_by: string | null; decided_at: string | null; decision_note: string; backtest: Backtest
}
export type Limit = { label: string; min: number; max: number; whole: boolean }
export type Scenario = {
  scenario: string; title: string; typology: string; version: string; revision: number; enabled: boolean; tunable: boolean
  defaults: Record<string, number>; params: Record<string, number>; limits: Record<string, Limit>
  record: { alerts: number; productive: number; unproductive: number; undecided: number; productive_rate: number | null }
  pending: Version | null; history: Version[]
}
export type Progress = { alerts: number; customers: number; cases: number; open: number; closed: number; productive: number; filings: number; by_outcome: Record<string, number>; case_ids: string[]; complete: number }
export type Lookback = {
  id: string; name: string; reason: string; scenario: string; scenario_title: string; params: Record<string, number>; defaults: Record<string, number>
  period_from: string; period_to: string; risk_ratings: string[]; status: string; due: string | null; overdue: boolean; created_by: string; created: string
  run_by: string | null; run_at: string | null; closed_by: string | null; closed_at: string | null; conclusion: string
  preview: { found: number; already_alerted: number; already: Record<string, number>; to_raise: number; customers: number; over_limit: boolean; raised?: number; cases?: number
    sample: { party_id: string; party_name: string; score: number; explanation: string }[] }
  progress: Progress; history: { at: string; by: string; what: string; note: string }[]
}
export type Desk = { scenarios: Scenario[]; lookbacks: Lookback[]; lookback_limit: number; can_propose: boolean; can_decide: boolean; can_sign_off: boolean; me: string }

export const OUTCOME: Record<Outcome, string> = { productive: 'Led to a filing or confirmed concern', unproductive: 'Closed with nothing found', undecided: 'Still being worked', never_raised: 'Never raised' }
/** A setting as people read it: money with separators, shares as percentages. */
export const show = (key: string, v: number) => (key.includes('ratio') ? `${Math.round(v * 100)}%` : v >= 1000 ? v.toLocaleString('en-US') : String(v))
