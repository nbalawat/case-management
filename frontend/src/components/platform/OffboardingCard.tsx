import { useState } from 'react'
import { api, fmtDateTime } from '../../api'
import { useApi } from '../../useApi'
import { Card, Loading, Pill } from '../ui'

type Client = { id: string; name: string; status: string }
type Offboard = {
  id: string; tenant: string; name: string; status: 'exported' | 'handed_over' | 'completed'; exported_by: string; exported_at: string
  export_bytes: number; export_sha256: string; counts: { tables: Dict<number>; objects: Dict<number> }; downloaded_by: string | null
  downloaded_at: string | null; completed_by: string | null; completed_at: string | null; erased: Dict<Dict<unknown>>
  remains: { what: string; where: string; why: string }[]; error: string
}
type Dict<T> = { [k: string]: T }

const TONE = { exported: 'warning', handed_over: 'warning', completed: 'good' } as const
const LABEL = { exported: 'exported, not yet handed over', handed_over: 'handed over', completed: 'erased' }
const mb = (b: number) => (b < 1e6 ? `${(b / 1e3).toFixed(0)} kB` : `${(b / 1e6).toFixed(1)} MB`)
const total = (o: Dict<number>) => Object.values(o).reduce((a, b) => a + b, 0)

/** A client leaving: export its data, hand the bundle over, then erase the client everywhere the platform keeps it. */
export default function OffboardingCard({ clients, onChange }: { clients: Client[]; onChange: () => void }) {
  const { data, error, reload } = useApi<{ offboardings: Offboard[] }>('/platform/offboarding')
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<Dict<string>>({})
  if (!data) return <Card title="Offboarding"><Loading error={error} /></Card>
  const run = (label: string, work: () => Promise<string>) => {
    setBusy(label); setMsg(null)
    work().then(setMsg).catch(e => setMsg(e.message)).finally(() => { setBusy(null); reload(); onChange() })
  }
  const latest = (t: string) => data.offboardings.find(o => o.tenant === t && o.status !== 'completed')
  const suspended = clients.filter(c => c.status === 'suspended')
  return (
    <Card title="Offboarding" action={<span className="muted small">Suspend a client first, so nothing changes while it is exported</span>}>
      <p className="muted small">The export holds every row, document, landing-zone file, quarantined record and lake table the client has here, with a SHA-256 for each file in its manifest. It is stored sealed under the client's key. Erasing comes only after the bundle is downloaded, needs the client's name typed, and refuses if anything changed since the export. It removes the client from the database, documents, backups of the client alone, the lake, search, the payment graph, the event bus and workflow history, then destroys its key.</p>
      {msg && <p className="msg" role="status">{msg}</p>}
      {suspended.length === 0 ? <p className="empty">No client is suspended.</p> : (
        <table><thead><tr><th>Client</th><th>Export</th><th>Erase</th></tr></thead>
          <tbody>{suspended.map(c => {
            const o = latest(c.id)
            return (
              <tr key={c.id}><td><b>{c.name}</b><div className="muted small">{c.id}</div></td>
                <td>{o ? <>
                  <Pill tone={TONE[o.status]}>{LABEL[o.status]}</Pill>
                  <div className="muted small">{fmtDateTime(o.exported_at)}, {mb(o.export_bytes)}, {total(o.counts.tables).toLocaleString('en-US')} rows, {total(o.counts.objects)} files</div>
                  {o.downloaded_at && <div className="muted small">downloaded by {o.downloaded_by} {fmtDateTime(o.downloaded_at)}</div>}
                  {o.error && <div className="small">Last attempt stopped: {o.error}</div>}
                  <button className="ghost" disabled={!!busy} onClick={() => run(`dl-${o.id}`, () => api.download(`/platform/offboarding/exports/${o.id}/download`).then(() => `${c.name}: bundle downloaded and checked against its fingerprint.`))}>Download</button>
                </> : <span className="muted small">none yet</span>}
                  <button disabled={!!busy} onClick={() => run(`ex-${c.id}`, () => api.post<Offboard>(`/platform/offboarding/${c.id}/export`, {}).then(r => `${c.name}: exported ${mb(r.export_bytes)}. Download it to hand it over.`))}>
                    {busy === `ex-${c.id}` ? 'Exporting…' : o ? 'Export again' : 'Export'}</button></td>
                <td>{o?.status === 'handed_over' ? <span className="toolbar">
                  <input aria-label={`Type ${c.name} to confirm`} placeholder={`Type ${c.name}`} value={confirm[c.id] ?? ''} onChange={e => setConfirm({ ...confirm, [c.id]: e.target.value })} />
                  <button className="danger" disabled={!!busy || confirm[c.id] !== c.name} onClick={() => run(`er-${c.id}`, () => api.post<Offboard>(`/platform/offboarding/${c.id}/erase`, { confirm: confirm[c.id] })
                    .then(r => `${c.name} is erased. ${total(r.erased.database as Dict<number>).toLocaleString('en-US')} rows removed and its key destroyed.`))}>
                    {busy === `er-${c.id}` ? 'Erasing…' : 'Erase for good'}</button></span> : <span className="muted small">after the hand-over</span>}</td></tr>)
          })}</tbody></table>)}
      {data.offboardings.some(o => o.status === 'completed') && <>
        <h3>Clients that have left</h3>
        <table><thead><tr><th>Client</th><th>Erased</th><th>What remains</th></tr></thead>
          <tbody>{data.offboardings.filter(o => o.status === 'completed').map(o => (
            <tr key={o.id}><td><b>{o.name}</b><div className="muted small">{o.tenant} · export {o.export_sha256.slice(0, 12)}…</div></td>
              <td className="small">{o.completed_at && fmtDateTime(o.completed_at)} by {o.completed_by}<div className="muted">{Object.keys(o.erased).filter(k => (o.erased[k] as Dict<unknown>).configured !== false).join(', ')}</div></td>
              <td className="small">{o.remains.map(r => <div key={r.what}><b>{r.what}</b>: {r.why}</div>)}</td></tr>))}</tbody></table>
      </>}
    </Card>
  )
}
