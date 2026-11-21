/** Shapes of /api/developer. */
export type Scope = { name: string; description: string; needs_permission: string | null; people_only: boolean }
export type Role = { id: string; title: string; permissions: string[]; read_only: boolean; sees: string }
export type Usage = { calls: number; errors: number; throttled: number; p95_ms: number | null; by_status: Record<string, number> }
export type App = {
  client_id: string; name: string; description: string; roles: string[]; queues: string[]; scopes: string[]; allowed_ips: string[]
  rate_limit: number; token_ttl: number; status: 'active' | 'suspended' | 'revoked'; created_by: string; created: string
  expires: string | null; last_used: string | null; usage?: Usage; webhooks?: number
}
export type Delivery = { id: string; event_type: string; status: string; attempts: number; last_code: number | null; last_error: string | null; created: string }
export type Hook = { id: string; url: string; event_types: string[]; status: string; disabled_reason: string | null; recent: Delivery[] }
export type AppDetail = App & {
  usage: Usage
  secrets: { id: string; hint: string; created: string; expires: string | null; live: boolean; last_used: string | null }[]
  history: { at: string; by: string; what: string }[]
  calls: { ts: string; method: string; route: string; status: number; ms: number; request_id: string; ip: string }[]
  webhooks: Hook[]
}
export type Overview = {
  api: { base_url: string; version: string; openapi: string; docs: string; token_url: string }
  scopes: Scope[]; roles: Role[]; queues: { id: string; label: string }[]; clients: App[]; people_usage: Usage; rules: string[]
}
