/** The workflow designer's data, mirroring backend/app/composer. A design is plain JSON: blocks (nodes), steps (edges), settings. */

export type Cond = { fact: string; op: string; value: string | number | boolean }
export type CondGroup = { match?: 'all' | 'any'; conditions: Cond[]; raw?: unknown }
export type Branch = CondGroup & { id: string; label: string }
export type Task = { id?: string; label: string; required?: boolean }
export type Action = { do: string; [k: string]: unknown }

export type NodeConfig = {
  label?: string
  queue?: string | null
  sla_hours?: number | null
  description?: string
  tasks?: Task[]
  actions?: Action[]
  scenarios?: string[]
  disposition?: string
  approvers?: string[]
  four_eyes?: boolean
  branches?: Branch[]
  otherwise?: boolean
  otherwise_label?: string
  state_id?: string
  [k: string]: unknown
}

export type DNode = { id: string; type: string; position: { x: number; y: number }; config: NodeConfig }
export type StepConfig = { label?: string; roles?: string[]; requires?: string[]; four_eyes?: boolean; when?: CondGroup }
export type DEdge = { id: string; source: string; target: string; handle?: string | null; config: StepConfig }

export type Settings = {
  deadline_label: string; deadline_days: number; escalation_raise_priority: boolean; escalation_notify: string
  qa_sample_rate: number; qa_dispositions: string[]; qa_reopen_to: string | null; agents: string[]; autonomy: string
}

export type Design = {
  format?: string; key: string; title: string; domain: string; description: string; version: string
  nodes: DNode[]; edges: DEdge[]; settings: Settings
}

export type FieldSpec = {
  key: string; label: string; kind: 'text' | 'textarea' | 'number' | 'select' | 'multiselect' | 'bool'
  required: boolean; options: string | { id: string; label: string }[]; default: unknown; help: string
}
export type Preset = { id: string; label: string; description: string; config: NodeConfig; icon: string }
export type BlockSpec = {
  id: string; kind: 'start' | 'stage' | 'decision' | 'outcome'; category: string; label: string; description: string; icon: string
  fields: FieldSpec[]; defaults: NodeConfig; accepts_actions: boolean; accepts_tasks: boolean; presets: Preset[]
}
export type ActionSpec = { id: string; label: string; description: string; icon: string; fields: FieldSpec[] }
export type Option = { id: string; label: string; [k: string]: unknown }
export type Fact = { id: string; label: string; kind: 'number' | 'bool' | 'choice' | 'text'; values: string[] }
export type Format = { id: string; label: string; extension: string; media_type: string; description: string }

export type Catalog = {
  categories: string[]; blocks: BlockSpec[]; actions: ActionSpec[]; step_fields: FieldSpec[]; branch_step_fields: FieldSpec[]
  formats: Format[]
  reference: {
    roles: (Option & { people: string[] })[]
    queues: (Option & { roles: string[]; people: string[] })[]
    requirements: Option[]; facts: Fact[]; agents: Option[]; recipients: Option[]
    scenarios: (Option & { owner: string | null })[]
    dispositions: (Option & { does: string; reasons: number })[]
    autonomy: (Option & { help: string })[]
    forms: Option[]; decision_tables: Option[]
    live: { id: string; title: string; version: string }[]
  }
}

export type Issue = { level: 'error' | 'warning'; target: 'node' | 'edge' | 'workflow'; id: string | null; message: string; hint: string }
export type Check = {
  ok: boolean; issues: Issue[]; errors: number; warnings: number; yaml?: string; changes?: string[]; is_new?: boolean
  summary?: { stages: number; outcomes: number; steps: number; automatic_actions: number }
}

export type DraftSummary = {
  id: string; case_type: string; title: string; status: 'draft' | 'published' | 'archived'; revision: number; origin: string
  created_by: string; created: string; updated_by: string; updated: string; published_version: string | null; version: string; blocks: number
}
export type Draft = DraftSummary & { design: Design }
export type Template = { id: string; title: string; summary: string; domain: string; tags: string[]; kind: 'template' | 'case_type' }

export type Sample = { name: string; amount: number; risk_rating: string; pep: boolean; priority: string }
export type TrialOption = { to: string; to_label: string; label: string; who: string[]; four_eyes: boolean; requires: string[]; when: string }
export type TrialStep = { ok: boolean; to: string; label?: string; by?: string; why?: string; prepared?: string[]; stage?: string }
export type Trial = {
  ready: boolean; ok?: boolean; why?: string; issues?: Issue[]
  case?: { id: string; stage: string; stage_label: string; queue: string | null; queue_label: string; closed: boolean; disposition: string | null; priority: string }
  steps?: TrialStep[]; stopped?: TrialStep | null; options?: TrialOption[]
  timeline?: { ts: string; actor: string; actor_kind: string; kind: string; text: string }[]
  routes?: { routes: { steps: { from: string; to: string; label: string }[]; outcome: string }[]; outcomes_reached: string[]; outcomes_not_reached: string[]; truncated: boolean }
  state_nodes?: Record<string, string>
  facts?: Record<string, unknown>
}
