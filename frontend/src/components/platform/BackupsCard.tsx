import { useState } from 'react'
import { api, fmtDateTime } from '../../api'
import { useApi } from '../../useApi'
import { Card, Loading, Pill } from '../ui'

type Backup = { id: string; created: string; scope: string; schema: string; tables: number; rows: number; documents: number; document_bytes: number; seconds: number }
type Drill = { backup: string; started: string; ok: boolean; tables: number; rows: number; mismatched: string[]; backup_seconds: number; restore_seconds: number; verify_seconds: number; source: string; into: string }
type Listing = { location: string; backups: Backup[]; drills: Drill[] }

const n = (v: number) => v.toLocaleString('en-US')
const mb = (b: number) => (b < 1e6 ? `${(b / 1e3).toFixed(0)} kB` : `${(b / 1e6).toFixed(1)} MB`)

/** Backups and restore drills, for platform staff. Restoring is a command, on purpose: it writes a whole database. */
export default function BackupsCard() {
  const { data, error, reload } = useApi<Listing>('/platform/backups')
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const act = (label: string, run: () => Promise<string>) => {
    setBusy(label); setMsg(null)
    run().then(setMsg).catch(e => setMsg(e.message)).finally(() => { setBusy(null); reload() })
  }
  const last = data?.drills[0]
  return (
    <Card title="Backups and restore drills" action={<span className="toolbar">
      {last && (last.ok ? <Pill tone="good">last drill passed</Pill> : <Pill tone="critical">last drill failed</Pill>)}
      <button disabled={!!busy} onClick={() => act('backup', () => api.post<Backup>('/platform/backups', {}).then(b => `Backed up ${n(b.rows)} rows and ${b.documents} documents in ${b.seconds} s.`))}>
        {busy === 'backup' ? 'Backing up…' : 'Back up now'}</button>
      <button className="primary" disabled={!!busy} onClick={() => act('drill', () => api.post<Drill>('/platform/backups/drill', {}).then(d =>
        d.ok ? `Drill passed: ${d.tables} tables, ${n(d.rows)} rows restored and identical. Backup ${d.backup_seconds} s, restore ${d.restore_seconds} s.`
          : `Drill failed: ${d.mismatched.join(', ')} differ after restore.`))}>
        {busy === 'drill' ? 'Running drill…' : 'Run a restore drill'}</button></span>}>
      {!data ? <Loading error={error} /> : <>
        {msg && <p className="msg" role="status">{msg}</p>}
        {last && <p className="small">
          Last drill {fmtDateTime(last.started)}: backed up {n(last.rows)} rows from {last.source} in {last.backup_seconds} s, checked every file in {last.verify_seconds} s,
          restored into a scratch {last.into} database in {last.restore_seconds} s and compared all {last.tables} tables{last.ok ? ', which were identical.' : `: ${last.mismatched.join(', ')} differed.`}
        </p>}
        {data.backups.length === 0 ? <p className="empty">No backups yet.</p> : (
          <div className="scroll"><table>
            <thead><tr><th>Backup</th><th>Of</th><th className="num">Rows</th><th className="num">Documents</th><th>Schema</th><th className="num">Took</th></tr></thead>
            <tbody>{data.backups.slice(0, 10).map(b => (
              <tr key={b.id}><td className="small nowrap">{fmtDateTime(b.created)}<div className="muted">{b.id}</div></td>
                <td>{b.scope === 'platform' ? 'Whole platform' : <>Client <b>{b.scope}</b></>}</td>
                <td className="num">{n(b.rows)}<div className="muted small">{b.tables} tables</div></td>
                <td className="num">{b.documents}<div className="muted small">{mb(b.document_bytes)}</div></td>
                <td>{b.schema}</td><td className="num">{b.seconds} s</td></tr>))}</tbody>
          </table></div>)}
        <p className="muted small">
          Backups are written by the application to the {data.location} location in BACKUP_URL, the same on any cloud, and restore into Postgres or SQLite.
          They hold the whole platform or one client. Restoring is a command, not a button: <code>python -m app.backup restore ID --into DATABASE_URL</code>.
          It refuses a database that already holds the data and never overwrites a document.
        </p>
      </>}
    </Card>
  )
}
