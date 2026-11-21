import type { CaseRow } from '../../../pages/Cases'

export type Finding = { text: string; cites: string[]; sources?: string[] }
export type Step = { agent: string; title: string; summary: string; findings: Finding[]; outcome?: string; level?: string; rejected?: number }
export type Txn = { id: string; ts: string; amount: number; direction: string; channel: string; counterparty: string; country: string; alerted: boolean }
export type Transition = { to: string; label: string; to_label: string; needs_note: boolean; blocked: string | null; when: string; four_eyes: boolean; reasons: { code: string; label: string }[] }

/** GET /api/cases/{id} */
export type CaseDetail = {
  case: CaseRow & { narrative: string; drafted_by: string | null; type_version: string }
  party: { id: string; name: string; kind: string; occupation: string; country: string; risk_rating: string; pep: boolean; onboarded: string; accounts: string[] }
  alerts: { id: string; title: string; version: string; typology: string; score: number; explanation: string; created: string; source: string; txn_ids: string[] }[]
  transactions: Txn[]
  agent_run: null | { provider: string; recommendation: string; confidence: number; steps: Step[] }
  events: { id: number; ts: string; actor: string; actor_kind: string; kind: string; payload: Record<string, unknown> }[]
  transitions: Transition[]
  tasks: { id: string; label: string; required: boolean; done: boolean; done_by: string | null; done_at: string | null }[]
  requests: { id: string; recipient_label: string; question: string; status: string; raised_by: string; created: string; due: string; response: string; answered: string | null; answered_by: string | null }[]
  workflow: { states: { id: string; label: string; kind: string }[]; autonomy: string; recipients: Record<string, string> }
  access: { can_work: boolean; can_claim: boolean; can_reassign: boolean; read_only: boolean; can_restrict: boolean; restricted_reason: string; queue_members: { id: string; name: string }[]; queue_label: string }
  parent: CaseRow | null; children: CaseRow[]
  filing: null | { id: string; kind: string; filed_by: string; filed_at: string; sha256: string; status: string; reference: string; problems: string[] }
  other_cases: CaseRow[]
}

/** Runs a case action, then reloads the case and every panel that depends on it, and reports the outcome. */
export type Act = (fn: () => Promise<unknown>, ok: string) => Promise<void>

export type RequestDraft = { recipient: string; question: string; due_days: number }
export const NEW_REQUEST: RequestDraft = { recipient: 'customer', question: '', due_days: 5 }
