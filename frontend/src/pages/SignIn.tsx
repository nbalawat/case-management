import { useState } from 'react'
import { beginSignIn, devIdp, signOut, type WhoAmI } from '../auth'

/** Shown when sign-in is on and nobody is signed in. The user names their organisation and is sent to its own sign-in page. */
export default function SignIn({ error }: { error: string | null }) {
  const [org, setOrg] = useState(sessionStorage.getItem('signin:org') || '')
  const [problem, setProblem] = useState(error)
  const [busy, setBusy] = useState(false)
  const go = (name: string) => {
    if (!name.trim()) return
    setBusy(true)
    sessionStorage.setItem('signin:org', name.trim())
    beginSignIn(name).catch(e => { setProblem(e.message); setBusy(false) })
  }
  return (
    <div className="signin">
      <form className="card" onSubmit={e => { e.preventDefault(); go(org) }}>
        <div className="brand"><span className="mark" aria-hidden />CaseForge</div>
        <h1>Sign in</h1>
        <p className="muted">Enter your organisation. You will sign in on its own page and come straight back.</p>
        <label>Organisation<input autoFocus value={org} onChange={e => { setOrg(e.target.value); setProblem(null) }} placeholder="for example meridian" autoComplete="organization" /></label>
        {problem && <p className="error" role="alert">{problem}</p>}
        <button className="primary" disabled={busy || !org.trim()}>{busy ? 'Opening sign-in…' : 'Continue'}</button>
        <button type="button" className="link" onClick={() => go('platform')}>Platform staff sign in here</button>
        {devIdp() && <p className="hint">This environment uses the built-in development identity provider. Try <b>meridian</b>, <b>kestrel</b> or <b>alder</b>.</p>}
      </form>
    </div>
  )
}

/** Signed in at the identity provider, but the client has not given this person a role. */
export function NoRole({ who }: { who: WhoAmI }) {
  return (
    <div className="signin">
      <div className="card">
        <div className="brand"><span className="mark" aria-hidden />CaseForge</div>
        <h1>You are signed in, but have no role yet</h1>
        <p className="muted">
          {who.tenant ? <>You signed in to <b>{who.tenant.name}</b> as <b>{who.subject}</b>. </> : <>You signed in as <b>{who.subject}</b>. </>}
          An administrator needs to add you on the Access page before you can see anything.
        </p>
        <button className="primary" onClick={signOut}>Sign out</button>
      </div>
    </div>
  )
}
