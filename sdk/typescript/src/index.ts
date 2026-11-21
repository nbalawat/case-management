/**
 * CaseForge public API client for Node 20+, Deno, Bun and browsers. It gets and renews tokens, follows cursors, adds an
 * Idempotency-Key to every write, waits out 429 with Retry-After, and turns problem documents into ApiError.
 *
 *   const cf = new CaseForge({ baseUrl, clientId, clientSecret, scopes: ['cases:read'] })
 *   for await (const c of cf.paginate('/cases', { status: 'open' })) console.log(c.id)
 */
export type Page<T> = { data: T[]; next_cursor: string | null; has_more: boolean }
export type Problem = { type: string; title: string; status: number; detail: string; code: string; request_id?: string }
export type Options = {
  baseUrl: string
  clientId?: string
  clientSecret?: string
  /** Call as a person instead, with the token from your identity provider. */
  token?: string
  scopes?: string[]
  maxRetries?: number
  fetch?: typeof fetch
}

export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, readonly detail: string, readonly requestId?: string, readonly body?: unknown) {
    super(`${status} ${code}: ${detail}${requestId ? ` (request ${requestId})` : ''}`)
  }
}

const WRITES = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
const RETRY = new Set([429, 502, 503, 504])
const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))
type Query = Record<string, string | number | boolean | undefined | null>

export class CaseForge {
  private readonly base: string
  private token?: string
  private expires = 0
  private readonly f: typeof fetch

  constructor(private readonly o: Options) {
    if (!o.token && !(o.clientId && o.clientSecret)) throw new Error('Give a client id and secret, or a person\'s token')
    this.base = o.baseUrl.replace(/\/$/, '') + '/api/v1'
    this.f = o.fetch ?? fetch
    if (o.token) { this.token = o.token; this.expires = Infinity }
  }

  private async bearer(): Promise<string> {
    if (this.token && Date.now() < this.expires - 30_000) return this.token
    const form = new URLSearchParams({ grant_type: 'client_credentials', ...(this.o.scopes ? { scope: this.o.scopes.join(' ') } : {}) })
    const r = await this.f(`${this.base}/oauth/token`, {
      method: 'POST', body: form,
      headers: { Authorization: 'Basic ' + btoa(`${this.o.clientId}:${this.o.clientSecret}`), 'Content-Type': 'application/x-www-form-urlencoded' },
    })
    if (!r.ok) throw await CaseForge.error(r)
    const body = await r.json() as { access_token: string; expires_in: number }
    this.token = body.access_token
    this.expires = Date.now() + body.expires_in * 1000
    return this.token
  }

  private static async error(r: Response): Promise<ApiError> {
    const b = await r.json().catch(() => ({})) as Partial<Problem> & { error?: string }
    return new ApiError(r.status, b.code ?? b.error ?? 'error', b.detail ?? r.statusText, b.request_id ?? r.headers.get('x-request-id') ?? undefined, b)
  }

  async request<T = unknown>(method: string, path: string, opts: { query?: Query; body?: unknown; idempotencyKey?: string; headers?: Record<string, string> } = {}): Promise<T> {
    const url = new URL(this.base + path)
    for (const [k, v] of Object.entries(opts.query ?? {})) if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
    const headers: Record<string, string> = { ...opts.headers }
    if (opts.body !== undefined) headers['Content-Type'] = 'application/json'
    if (WRITES.has(method.toUpperCase())) headers['Idempotency-Key'] = opts.idempotencyKey ?? crypto.randomUUID()
    const tries = this.o.maxRetries ?? 3
    for (let attempt = 0; ; attempt++) {
      headers.Authorization = `Bearer ${await this.bearer()}`
      const r = await this.f(url, { method, headers, body: opts.body === undefined ? undefined : JSON.stringify(opts.body) })
      if (r.status === 401 && this.o.clientId && attempt === 0) { this.token = undefined; continue }
      if (RETRY.has(r.status) && attempt < tries) { await sleep(Math.min(Number(r.headers.get('retry-after') ?? 2 ** attempt), 30) * 1000); continue }
      if (!r.ok) throw await CaseForge.error(r)
      return (r.status === 204 ? undefined : await r.json()) as T
    }
  }

  get<T = unknown>(path: string, query?: Query) { return this.request<T>('GET', path, { query }) }
  post<T = unknown>(path: string, body: unknown = {}, idempotencyKey?: string) { return this.request<T>('POST', path, { body, idempotencyKey }) }
  put<T = unknown>(path: string, body: unknown, ifMatch?: string) { return this.request<T>('PUT', path, { body, headers: ifMatch ? { 'If-Match': ifMatch } : {} }) }

  /** Every item of a cursor-paginated list. */
  async *paginate<T = Record<string, unknown>>(path: string, query: Query = {}): AsyncGenerator<T> {
    let cursor: string | null = null
    do {
      const page: Page<T> = await this.get<Page<T>>(path, { ...query, ...(cursor ? { cursor } : {}) })
      yield* page.data
      cursor = page.next_cursor
    } while (cursor)
  }

  /** One page of the event feed and the sequence to continue from. */
  async events(after = 0, types?: string[], limit = 200) {
    const p = await this.get<{ data: Record<string, unknown>[]; next_after: number }>('/events', { after, types: types?.join(','), limit })
    return { events: p.data, after: p.next_after }
  }
}

/** Check a webhook delivery (Standard Webhooks). Pass the raw body text, before parsing it. */
export async function verifyWebhook(secret: string, headers: Record<string, string>, rawBody: string, nowSeconds = Date.now() / 1000): Promise<boolean> {
  const h = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]))
  const ts = Number(h['webhook-timestamp'])
  if (!Number.isFinite(ts) || Math.abs(nowSeconds - ts) > 300) return false
  const key = Uint8Array.from(atob(secret.replace(/^whsec_/, '')), c => c.charCodeAt(0))
  const k = await crypto.subtle.importKey('raw', key, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign'])
  const mac = new Uint8Array(await crypto.subtle.sign('HMAC', k, new TextEncoder().encode(`${h['webhook-id']}.${ts}.${rawBody}`)))
  const expected = 'v1,' + btoa(String.fromCharCode(...mac))
  const given = (h['webhook-signature'] ?? '').split(' ')
  let ok = false
  for (const s of given) if (s.length === expected.length) { let d = 0; for (let i = 0; i < s.length; i++) d |= s.charCodeAt(i) ^ expected.charCodeAt(i); ok ||= d === 0 }
  return ok
}
