// Thin API client. In this demo the active client and person travel in headers; a real deployment takes both
// from the sign-in token, so a user can never name a client they do not belong to.
export type Persona = { id: string; name: string; role: string; title: string; about: string; read_only: boolean; permissions: string[]; org?: 'client' | 'provider' }

import { authMode, bearer, signOut } from './auth'

export const OPERATOR = '@operator' // the provider's own staff, who work across clients
let tenantId = localStorage.getItem('tenant') || 'meridian'
const personaKey = () => `persona:${tenantId}`
export const getTenantId = () => tenantId
export const isOperator = () => tenantId === OPERATOR
export const setTenantId = (id: string) => { tenantId = id; localStorage.setItem('tenant', id) }
export const getPersonaId = () => localStorage.getItem(personaKey()) || ''
// Signed in as a member of the provider's staff: the token says who, and the client they are working at travels in
// X-Tenant. The server lets that through only where the client has approved an engagement for them.
let staffMode = false
export const setStaffMode = (on: boolean) => { staffMode = on }
export const isStaff = () => staffMode
export const setPersonaId = (id: string) => localStorage.setItem(personaKey(), id)

function identity(): Record<string, string> {
  if (authMode() === 'oidc') return { ...bearer(), ...(staffMode && !isOperator() ? { 'X-Tenant': tenantId } : {}) } // the token says who
  if (isOperator()) return { 'X-Operator': 'olivia' }
  const who = getPersonaId()
  return { 'X-Tenant': tenantId, ...(who ? { 'X-Persona': who } : {}) }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(`/api${path}`, { ...init, headers: { 'Content-Type': 'application/json', ...identity(), ...(init?.headers || {}) } })
  if (r.status === 401 && authMode() === 'oidc') signOut() // the token expired or was withdrawn
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail || `Request failed (${r.status})`)
  }
  return r.json()
}

/** Fetch a file as the signed-in person and hand it to the browser to save. */
async function download(path: string) {
  const r = await fetch(`/api${path}`, { headers: identity() })
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail || `Download failed (${r.status})`)
  }
  const name = /filename="([^"]+)"/.exec(r.headers.get('Content-Disposition') ?? '')?.[1] ?? 'document'
  const url = URL.createObjectURL(await r.blob())
  const a = Object.assign(document.createElement('a'), { href: url, download: name })
  a.click()
  URL.revokeObjectURL(url)
}

export const api = {
  download,
  get: <T,>(path: string) => req<T>(path),
  post: <T,>(path: string, body?: unknown) => req<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  put: <T,>(path: string, body?: unknown) => req<T>(path, { method: 'PUT', body: JSON.stringify(body ?? {}) }),
}

export const fmtMoney = (n: number) => n.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
export const fmtDate = (iso: string) => new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
export const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
export const daysUntil = (iso: string | null) => (iso ? Math.ceil((new Date(iso).getTime() - Date.now()) / 86400000) : null)
export const hoursUntil = (iso: string | null) => (iso ? Math.round((new Date(iso).getTime() - Date.now()) / 3600000) : null)
export const words = (id: string) => id.replaceAll('_', ' ')
