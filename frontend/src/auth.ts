// Sign-in. In demo mode there is none: the client and person pickers at the top right stand in for it.
// With AUTH_MODE=oidc the server signs the user in (authorization code with PKCE, done server-side) and keeps the
// tokens; the browser holds only an httpOnly session cookie that no script, this one included, can read. The server
// takes the client and the person from the session's token and from nothing else, and refreshes it as needed.

export type AuthConfig = { mode: 'demo' | 'oidc'; dev_idp: boolean }
type Staff = { id: string; name: string; title: string; console: boolean }
export type WhoAmI = { mode: string; subject?: string; known?: boolean; tenant?: { id: string; name: string }; operator?: Staff | null; staff?: Staff | null }

let config: AuthConfig = { mode: 'demo', dev_idp: false }
export const authMode = () => config.mode
export const devIdp = () => config.dev_idp

let leaving = false
/** Sign out here and at the identity provider, then land back on the sign-in page. */
export async function signOut() {
  if (leaving) return
  leaving = true
  const r = await fetch('/api/auth/logout', { method: 'POST' }).catch(() => null)
  const next = r?.ok ? (await r.json() as { redirect: string }).redirect : '/'
  window.location.assign(next || '/')
}

/** Off to the organisation's own sign-in page. The server finds it, and the provider sends the browser back to it. */
export async function beginSignIn(organisation: string) {
  const org = organisation.trim().toLowerCase()
  const found = await fetch(`/api/auth/provider/${encodeURIComponent(org)}`)
  if (!found.ok) throw new Error((await found.json().catch(() => ({}))).detail || 'That organisation does not sign in here')
  window.location.assign(`/api/auth/login?${new URLSearchParams({ organisation: org })}`)
}

/** Called once before the app renders. Returns who is signed in, or why nobody is. */
export async function initAuth(): Promise<{ who: WhoAmI | null; error: string | null }> {
  config = await (await fetch('/api/auth/config')).json()
  if (config.mode !== 'oidc') return { who: null, error: null }
  const q = new URLSearchParams(window.location.search)
  const error = q.get('signin_error')
  if (error) window.history.replaceState({}, '', '/')
  const r = await fetch('/api/auth/whoami')
  if (r.status === 401) return { who: null, error }
  if (!r.ok) return { who: null, error: 'Sign-in could not be checked. Please try again.' }
  const who = await r.json() as WhoAmI
  return who.subject ? { who, error: null } : { who: null, error }
}
