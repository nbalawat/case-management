export type Entry = { at: string; area: string; subject: string; what: string; by: string; second: string | null; note: string; link: string }
export type Item = {
  person: string; name: string; title: string; roles: string[]; permissions: string[]; queues: string[]; read_only: boolean; last_active: string | null; open_cases: number
  flags: string[]; decision: string | null; note: string; decided_by: string | null; decided_at: string | null; done: boolean | null
}
export type Review = {
  id: string; name: string; status: string; policy_version: string; started_by: string; started: string; due: string; overdue: boolean; closed_by: string | null; closed_at: string | null
  items: Item[]; people: number; decided: number; flagged: number; changes: number; outstanding: number
}
export type Access = { reviews: Review[]; can_certify: boolean; me: string; dormant_days: number; current: { flagged: number; people: number } | null }
export type Facets = { kinds: { kind: string; count: number }[]; actors: { actor: string; actor_kind: string; count: number }[] }
export type Governance = { register: Entry[]; areas: string[]; access: Access; audit: Facets }
