import { useState } from 'react'
import { api, fmtDateTime } from '../../api'
import { useApi } from '../../useApi'
import { Card, Loading, Pill } from '../ui'

type Client = {
  tenant: string; name: string; provider: string; sealed: boolean; version?: number; destroyed?: boolean; created?: string | null
  rotated?: string | null; reference?: string; summary: string; error?: string; documents: { sealed: number; not_sealed: number }
}
type Keys = { provider: string; clients: Client[] }

const PROVIDER: Record<string, string> = { local: 'Local keyring', transit: 'OpenBao / Vault Transit', none: 'None: documents stored as given' }

/** Each client's own key: which service holds it, its version, how many documents it seals, and rotating it. */
export default function ClientKeysCard() {
  const { data, error, reload } = useApi<Keys>('/platform/keys')
  const [msg, setMsg] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  if (!data) return <Card title="Client keys"><Loading error={error} /></Card>
  const rotate = (c: Client) => {
    setBusy(c.tenant)
    api.post<{ version: number; rewrapped: number }>(`/platform/keys/${c.tenant}/rotate`, {})
      .then(r => { setMsg(`${c.name}: now on key version ${r.version}. ${r.rewrapped} document${r.rewrapped === 1 ? '' : 's'} re-wrapped, so the old version protects nothing.`); reload() })
      .catch(e => setMsg(e.message)).finally(() => setBusy(null))
  }
  return (
    <Card title="Client keys" action={<span className="muted small">{PROVIDER[data.provider] ?? data.provider}</span>}>
      <p className="muted small">Every customer document is sealed under its own client's key before it reaches storage, so the storage provider, its backups and anyone who copies the bucket hold only ciphertext. Rotating re-wraps each document's data key under the new version without re-encrypting the document. Destroying a client's key at offboarding makes every copy of its documents unreadable.</p>
      {msg && <p className="msg" role="status">{msg}</p>}
      <table><thead><tr><th>Client</th><th>Key</th><th>Documents</th><th>Created</th><th /></tr></thead>
        <tbody>{data.clients.map(c => (
          <tr key={c.tenant}><td><b>{c.name}</b><div className="muted small">{c.reference}</div></td>
            <td>{c.error ? <Pill tone="critical">unavailable</Pill> : c.destroyed ? <Pill tone="critical">destroyed</Pill> : c.sealed && c.version ? <Pill tone="good">v{c.version}</Pill> : c.sealed ? <Pill tone="neutral">not made yet</Pill> : <Pill tone="warning">none</Pill>}
              <div className="muted small">{c.error ?? (c.rotated ? `rotated ${fmtDateTime(c.rotated)}` : c.summary)}</div></td>
            <td>{c.documents.sealed} sealed{c.documents.not_sealed > 0 && <div className="muted small">{c.documents.not_sealed} from before sealing</div>}</td>
            <td className="small">{c.created ? fmtDateTime(c.created) : '–'}</td>
            <td>{c.sealed && !!c.version && !c.destroyed && !c.error && <button disabled={busy === c.tenant} onClick={() => rotate(c)}>{busy === c.tenant ? 'Rotating…' : 'Rotate key'}</button>}</td></tr>))}</tbody></table>
    </Card>
  )
}
