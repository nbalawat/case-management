import { useState } from 'react'
import { api } from '../../api'
import { Card, Pill } from '../ui'

export type Provider = { issuer: string; audience: string; client_id: string; person_claim: string; jwks_uri: string | null }
type Client = { id: string; name: string; identity: Provider | null; identity_declared?: string | null }
const CLAIMS = ['preferred_username', 'email', 'upn', 'sub']
const EMPTY = { issuer: '', audience: '', client_id: '', person_claim: 'preferred_username', jwks_uri: '' }

/** Which identity provider each client's people sign in through. Registering one is the platform's job, not the client's. */
export default function IdentityProvidersCard({ clients, onChange }: { clients: Client[]; onChange: () => void }) {
  const [editing, setEditing] = useState<string | null>(null)
  const [form, setForm] = useState(EMPTY)
  const [msg, setMsg] = useState<string | null>(null)
  const open = (c: Client) => {
    setEditing(c.id); setMsg(null)
    setForm(c.identity ? { ...c.identity, jwks_uri: c.identity.jwks_uri ?? '' } : EMPTY)
  }
  const save = (c: Client, provider: typeof EMPTY | null) =>
    api.put(`/platform/tenants/${c.id}/identity`, { provider: provider && { ...provider, jwks_uri: provider.jwks_uri || null } })
      .then(() => { setMsg(provider ? `${c.name} now signs in through ${provider.issuer}.` : `${c.name} no longer has an identity provider.`); setEditing(null); onChange() })
      .catch(e => setMsg(e.message))
  const set = (k: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [k]: e.target.value })
  return (
    <Card title="Sign-in" action={<span className="muted small">Which identity provider signed a token decides which client it is for</span>}>
      <table><thead><tr><th>Client</th><th>Identity provider</th><th>Person from</th><th /></tr></thead>
        <tbody>{clients.map(c => <tr key={c.id}>
          <td><b>{c.name}</b><div className="muted small">{c.id}</div></td>
          <td className="small">{c.identity ? <><code>{c.identity.issuer}</code><div className="muted">audience {c.identity.audience}</div></> :
            c.identity_declared ? <><code>{c.identity_declared}</code><div className="muted">from the deployment's configuration (OIDC_CLIENTS)</div></> :
            <Pill tone="neutral">none registered</Pill>}</td>
          <td className="small">{c.identity?.person_claim ?? '–'}</td>
          <td className="nowrap"><button className="ghost" onClick={() => open(c)}>{c.identity ? 'Change' : 'Register'}</button>
            {c.identity && <button className="ghost" onClick={() => save(c, null)}>Remove</button>}</td>
        </tr>)}</tbody></table>
      {editing && (() => {
        const c = clients.find(x => x.id === editing)!
        return <form className="stack" onSubmit={e => { e.preventDefault(); save(c, form) }}>
          <h3>{c.identity ? 'Change' : 'Register'} {c.name}'s identity provider</h3>
          <label>Issuer<input required value={form.issuer} onChange={set('issuer')} placeholder="https://login.microsoftonline.com/<tenant>/v2.0" /></label>
          <label>Audience<input value={form.audience} onChange={set('audience')} placeholder="what the provider puts in tokens for this platform" /></label>
          <label>Client id<input value={form.client_id} onChange={set('client_id')} placeholder="the application registered at the provider (defaults to the audience)" /></label>
          <label>Person claim<select value={form.person_claim} onChange={set('person_claim')}>{CLAIMS.map(x => <option key={x}>{x}</option>)}</select></label>
          <label>Keys address (optional)<input value={form.jwks_uri} onChange={set('jwks_uri')} placeholder="only if the provider does not publish discovery" /></label>
          <div className="row"><button className="primary">Save</button><button type="button" className="ghost" onClick={() => setEditing(null)}>Cancel</button></div>
          <p className="muted small">The issuer must be https. Each client needs its own realm or tenant at the provider: one provider cannot speak for two clients.</p>
        </form>
      })()}
      {msg && <p className="msg" role="status">{msg}</p>}
    </Card>
  )
}
