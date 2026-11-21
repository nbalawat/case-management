import { useState } from 'react'
import { api } from '../../api'
import { Card } from '../ui'
import type { App, Overview } from './types'

type Made = App & { client_secret: string }

const Choice = ({ on, disabled, title, onClick, children }: { on: boolean; disabled?: boolean; title?: string; onClick: () => void; children: React.ReactNode }) => (
  <button type="button" className="dev-choice" aria-pressed={on} aria-disabled={disabled} title={title} onClick={() => !disabled && onClick()}>{children}</button>
)

/** Register an application: a name, the roles it acts under, and the scopes it may ask for. The secret is shown once. */
export default function Register({ data, onMade }: { data: Overview; onMade: (a: Made) => void }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [roles, setRoles] = useState<string[]>([])
  const [scopes, setScopes] = useState<string[]>(['cases:read'])
  const [ips, setIps] = useState('')
  const [rate, setRate] = useState(0)
  const [msg, setMsg] = useState<string | null>(null)
  const held = new Set(data.roles.filter(r => roles.includes(r.id)).flatMap(r => r.permissions))
  const why = (s: Overview['scopes'][number]) =>
    s.people_only ? 'Only with a person\'s own token' : s.needs_permission && !held.has(s.needs_permission) ? `The roles chosen do not hold ${s.needs_permission.replaceAll('_', ' ')}` : ''
  const toggle = (list: string[], set: (v: string[]) => void, v: string) => set(list.includes(v) ? list.filter(x => x !== v) : [...list, v])
  const submit = () => api.post<Made>('/developer/clients', {
    name, description, roles, scopes: scopes.filter(s => !why(data.scopes.find(x => x.name === s)!)),
    allowed_ips: ips.split(/[\s,]+/).filter(Boolean), rate_limit: rate,
  }).then(a => { setMsg(null); setName(''); setDescription(''); onMade(a) }).catch(e => setMsg(e.message))
  return (
    <Card title="Register an application">
      <div className="dev-form">
        <label>Name<input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="Case analytics warehouse sync" /></label>
        <label>What it does<textarea rows={2} value={description} onChange={e => setDescription(e.target.value)} /></label>
        <div><div className="muted small">Acts under the roles</div>
          <div className="dev-choices">{data.roles.map(r => (
            <Choice key={r.id} on={roles.includes(r.id)} title={`${r.sees}. ${r.permissions.join(', ') || 'No permissions'}`} onClick={() => toggle(roles, setRoles, r.id)}>{r.title}</Choice>))}
          </div></div>
        <div><div className="muted small">May ask for the scopes</div>
          <div className="dev-choices">{data.scopes.map(s => {
            const blocked = why(s)
            return <Choice key={s.name} on={scopes.includes(s.name) && !blocked} disabled={!!blocked} title={blocked || s.description}
              onClick={() => toggle(scopes, setScopes, s.name)}><code>{s.name}</code></Choice>
          })}</div></div>
        <div className="dev-grid">
          <label>Allowed addresses (CIDR, optional)<input type="text" value={ips} onChange={e => setIps(e.target.value)} placeholder="203.0.113.0/24" /></label>
          <label>Requests a minute (0 for the default)<input type="number" min={0} value={rate} onChange={e => setRate(Number(e.target.value))} /></label>
        </div>
        <div className="inline"><button className="primary" disabled={!name.trim() || !roles.length} onClick={submit}>Register</button>
          <span className="muted small">You will see the secret once. Store it in your secret manager.</span></div>
        {msg && <p className="msg" role="status">{msg}</p>}
      </div>
    </Card>
  )
}
