// Sign-in. In demo mode there is none: the client and person pickers at the top right stand in for it.
// With AUTH_MODE=oidc the browser sends the user to their organisation's identity provider (authorization code
// with PKCE) and from then on every request carries the signed token. The server takes the client and the person
// from that token and from nothing else.
const KEY = 'signin'
const PENDING = 'signin:pending'

export type AuthConfig = { mode: 'demo' | 'oidc'; dev_idp: boolean }
export type SignedIn = { token: string; expires: number; organisation: string }
export type WhoAmI = { mode: string; subject?: string; known?: boolean; tenant?: { id: string; name: string }; operator?: { id: string; name: string; title: string } | null }

let config: AuthConfig = { mode: 'demo', dev_idp: false }
export const authMode = () => config.mode
export const devIdp = () => config.dev_idp

export function session(): SignedIn | null {
  try {
    const s = JSON.parse(sessionStorage.getItem(KEY) || 'null') as SignedIn | null
    return s && s.expires > Date.now() ? s : null
  } catch { return null }
}
export const bearer = (): Record<string, string> => { const s = session(); return s ? { Authorization: `Bearer ${s.token}` } : {} }

export function signOut() {
  sessionStorage.removeItem(KEY)
  window.location.assign('/')
}

const b64url = (bytes: Uint8Array) => btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
const random = (n: number) => b64url(crypto.getRandomValues(new Uint8Array(n)))

export async function beginSignIn(organisation: string) {
  const org = organisation.trim().toLowerCase()
  const found = await fetch(`/api/auth/provider/${encodeURIComponent(org)}`)
  if (!found.ok) throw new Error((await found.json().catch(() => ({}))).detail || 'That organisation does not sign in here')
  const provider = await found.json() as { issuer: string; client_id: string; discovery: string }
  const meta = await (await fetch(provider.discovery)).json() as { issuer: string; authorization_endpoint: string; token_endpoint: string }
  if (meta.issuer !== provider.issuer) throw new Error('The identity provider did not identify itself correctly')
  const verifier = random(48), state = random(16), nonce = random(16)
  const challenge = b64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))))
  const redirect = `${window.location.origin}/`
  sessionStorage.setItem(PENDING, JSON.stringify({ org, verifier, state, nonce, redirect, token_endpoint: meta.token_endpoint, client_id: provider.client_id }))
  const q = new URLSearchParams({ response_type: 'code', client_id: provider.client_id, redirect_uri: redirect, scope: 'openid profile email', state, nonce,
    code_challenge: challenge, code_challenge_method: 'S256' })
  window.location.assign(`${meta.authorization_endpoint}?${q}`)
}

/** If the provider has just sent the user back with a code, exchange it for a token. */
async function completeSignIn(): Promise<string | null> {
  const q = new URLSearchParams(window.location.search)
  const code = q.get('code'), state = q.get('state'), failed = q.get('error')
  if (!code && !failed) return null
  const pending = JSON.parse(sessionStorage.getItem(PENDING) || 'null')
  sessionStorage.removeItem(PENDING)
  window.history.replaceState({}, '', '/')
  if (failed) return q.get('error_description') || 'Sign-in was cancelled'
  if (!pending || pending.state !== state) return 'Sign-in could not be completed. Please try again.'
  const r = await fetch(pending.token_endpoint, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'authorization_code', code: code!, redirect_uri: pending.redirect, client_id: pending.client_id, code_verifier: pending.verifier }) })
  if (!r.ok) return 'Sign-in could not be completed. Please try again.'
  const out = await r.json() as { access_token: string; id_token?: string; expires_in?: number }
  if (out.id_token) {
    const claims = JSON.parse(atob(out.id_token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')))
    if (claims.nonce !== pending.nonce) return 'Sign-in could not be completed. Please try again.'
  }
  sessionStorage.setItem(KEY, JSON.stringify({ token: out.access_token, expires: Date.now() + (out.expires_in ?? 3600) * 1000, organisation: pending.org }))
  return null
}

/** Called once before the app renders. Returns who is signed in, or why nobody is. */
export async function initAuth(): Promise<{ who: WhoAmI | null; error: string | null }> {
  config = await (await fetch('/api/auth/config')).json()
  if (config.mode !== 'oidc') return { who: null, error: null }
  const error = await completeSignIn()
  if (!session()) return { who: null, error }
  const r = await fetch('/api/auth/whoami', { headers: bearer() })
  if (!r.ok) { sessionStorage.removeItem(KEY); return { who: null, error: 'Your sign-in has expired' } }
  return { who: await r.json(), error: null }
}
