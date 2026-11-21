import { useState } from 'react'
import { api, fmtDateTime } from '../../api'
import { useApi } from '../../useApi'
import { Card, Loading, Pill } from '../ui'

type Person = {
  id: string; user_name: string; name: string; email: string; active: boolean; groups: string[]; roles: string[]
  status: string; updated: string
}
type Token = { id: string; hint: string; label: string; created_by: string; created: string; last_used: string | null; revoked: string | null }
type View = { people: Person[]; groups: Record<string, { roles: string[]; queues: string[] }>; sign_in: string; tokens: Token[] }

const TONE: Record<string, string> = { active: 'good', deactivated: 'neutral', 'named in the policy': 'human' }

/** Who the client's identity provider provisioned, what their groups give them here, and the SCIM tokens it uses. */
export default function DirectoryCard() {
  const { data, error, reload } = useApi<View>('/directory')
  const [label, setLabel] = useState('')
  const [made, setMade] = useState<{ token: string; label: string } | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  if (!data) return <Card title="Directory and sign-in"><Loading error={error} /></Card>
  const create = () => api.post<{ token: string; label: string }>('/directory/tokens', { label })
    .then(t => { setMade(t); setLabel(''); reload() }).catch(e => setMsg(e.message))
  const revoke = (t: Token) => api.del(`/directory/tokens/${t.id}`)
    .then(() => { setMsg(`Token ${t.hint} revoked. The identity provider can no longer provision with it.`); reload() }).catch(e => setMsg(e.message))
  const groups = Object.entries(data.groups)
  const live = data.tokens.filter(t => !t.revoked)
  return (
    <Card title="Directory and sign-in" action={<span className="muted small">{data.sign_in}</span>}>
      <p className="muted">Your identity provider provisions people and groups here over SCIM 2.0. Each group the access policy
        maps below gives its members those roles, so nobody has to be added one by one. Deactivating someone at the provider
        signs them out of here at once.</p>
      <h3>Groups that give a role</h3>
      {groups.length === 0 ? <p className="empty">The access policy has no <code>groups</code> section yet, so provisioned people get no role.</p> :
        <table><thead><tr><th>Group at the identity provider</th><th>Roles</th><th>Queues</th></tr></thead>
          <tbody>{groups.map(([g, x]) => <tr key={g}><td><b>{g}</b></td><td>{x.roles.map(r => <Pill key={r} tone="human">{r}</Pill>)}</td>
            <td className="small">{x.queues.join(', ') || <span className="muted">None</span>}</td></tr>)}</tbody></table>}
      <h3>Provisioned people ({data.people.length})</h3>
      {data.people.length === 0 ? <p className="empty">Nobody has been provisioned yet.</p> :
        <table><thead><tr><th>Person</th><th>Groups</th><th>Here</th><th>Changed</th></tr></thead>
          <tbody>{data.people.map(p => <tr key={p.id}>
            <td><b>{p.name || p.user_name}</b><div className="muted small">{p.user_name}</div></td>
            <td className="small">{p.groups.join(', ') || <span className="muted">None</span>}</td>
            <td><Pill tone={TONE[p.status] ?? 'warning'}>{p.status}</Pill> {p.roles.map(r => <Pill key={r} tone="human">{r}</Pill>)}</td>
            <td className="small nowrap">{fmtDateTime(p.updated)}</td></tr>)}</tbody></table>}
      <h3>SCIM tokens</h3>
      <p className="muted small">In your identity provider, set the tenant URL to <code>{`${window.location.origin}/api/scim/v2`}</code> and paste a token below as the secret.</p>
      {made && <p className="msg" role="status">Copy this token for {made.label || 'the identity provider'} now; it is not shown again: <code className="secret">{made.token}</code></p>}
      {live.length > 0 && <table><thead><tr><th>Token</th><th>Made by</th><th>Last used</th><th /></tr></thead>
        <tbody>{live.map(t => <tr key={t.id}><td><b>{t.label || 'Unlabelled'}</b><div className="muted small">…{t.hint}</div></td>
          <td className="small">{t.created_by}<div className="muted">{fmtDateTime(t.created)}</div></td>
          <td className="small">{t.last_used ? fmtDateTime(t.last_used) : <span className="muted">Never</span>}</td>
          <td><button className="ghost" onClick={() => revoke(t)}>Revoke</button></td></tr>)}</tbody></table>}
      <div className="row">
        <input type="search" placeholder="Which identity provider (e.g. Entra ID)" aria-label="Token label" value={label} onChange={e => setLabel(e.target.value)} />
        <button className="primary" onClick={create}>Make a SCIM token</button>
      </div>
      {msg && <p className="msg" role="status">{msg}</p>}
    </Card>
  )
}
