import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDate, fmtDateTime, words } from '../../api'
import { useNameOf } from '../../App'
import { Card, Pill } from '../ui'
import type { Doc, EvidenceView, Trigger } from './types'
import { OUTCOME } from './types'

const size = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} bytes`)
const toBase64 = (file: File) => new Promise<string>((resolve, reject) => {
  const r = new FileReader()
  r.onload = () => resolve(String(r.result).split(',')[1] ?? '')
  r.onerror = () => reject(new Error('The file could not be read'))
  r.readAsDataURL(file)
})

function DocRow({ d, view, onChange, say }: { d: Doc; view: EvidenceView; onChange: () => void; say: (m: string) => void }) {
  const nameOf = useNameOf()
  const [open, setOpen] = useState(false)
  const [kinds, setKinds] = useState<string[]>([])
  const [terms, setTerms] = useState('')
  const act = (path: string, body: unknown, ok: string) => api.post(`/evidence/${d.id}/${path}`, body).then(() => { say(ok); onChange() }).catch(e => say(e.message))
  const get = (redacted: boolean) => api.download(`/evidence/${d.id}/content${redacted ? '?redacted=true' : ''}`).then(onChange).catch(e => say(e.message))
  const current = d.status === 'current'
  return (
    <>
      <tr className={current ? '' : 'gone'}>
        <td><button className="cite" aria-expanded={open} onClick={() => setOpen(!open)}>{d.title || d.filename}</button><div className="muted small">{d.kind_label} · {d.filename} · {size(d.size)}</div></td>
        <td>{!current ? <Pill>{words(d.status)}</Pill> : d.expired ? <Pill tone="critical">Expired {fmtDate(d.expires!)}</Pill> : d.verified_by ? <Pill tone="good">Verified</Pill> : <Pill tone="warning">Not yet verified</Pill>}
          {current && !d.expired && d.expires && <div className="muted small">Expires {new Date(d.expires).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}</div>}
          {!d.custody_intact && <Pill tone="critical">Custody record altered</Pill>}</td>
        <td>{nameOf(d.uploaded_by)}<div className="muted small">{fmtDate(d.uploaded_at)}{d.case_id && <> · <Link to={`/cases/${d.case_id}`}>{d.case_id}</Link></>}</div></td>
        <td className="nowrap"><button className="ghost" onClick={() => get(false)}>Download</button>{d.redacted && <button className="ghost" onClick={() => get(true)}>Redacted copy</button>}</td>
      </tr>
      {open && <tr className="doc-detail"><td colSpan={4}>
        <div className="small"><b>Fingerprint</b> <code>sha256 {d.sha256}</code></div>
        {d.redacted && <div className="small">A redacted copy exists with {d.redactions.map(r => `${r.count} ${words(r.kind)}${r.count === 1 ? '' : 's'}`).join(', ')} removed. The original is unchanged.</div>}
        <ol className="custody">{d.custody.map((c, i) => <li key={i}><b>{words(c.action)}</b> by {nameOf(c.by)}, {fmtDateTime(c.at)}{c.detail ? `: ${c.detail}` : ''}</li>)}</ol>
        {view.can_change && current && <div className="inline wrap">
          {!d.verified_by && <button onClick={() => act('verify', {}, 'Verified. You are on record as having checked it.')}>Mark as verified</button>}
          <button onClick={() => act('withdraw', { why: 'Withdrawn from the customer page' }, 'Withdrawn. It stays on record, and no longer counts.')}>Withdraw</button>
          {d.redactable && <span className="redact">
            {view.patterns.map(p => <label key={p.id} className="check"><input type="checkbox" checked={kinds.includes(p.id)} onChange={e => setKinds(e.target.checked ? [...kinds, p.id] : kinds.filter(k => k !== p.id))} /> {p.label}</label>)}
            <input type="search" aria-label="Other words to remove" placeholder="Other words, comma separated" value={terms} onChange={e => setTerms(e.target.value)} />
            <button disabled={!kinds.length && !terms.trim()} onClick={() => act('redact', { kinds, terms: terms.split(',').map(t => t.trim()).filter(Boolean) }, 'Redacted copy made. The original is unchanged.')}>Make a redacted copy</button>
          </span>}
          {!d.redactable && <span className="muted small">Only text documents can be redacted here. Scans and PDFs need a redaction tool.</span>}
        </div>}
      </td></tr>}
    </>
  )
}

/** The customer's documents: what was provided, who checked it, when it runs out, and every time it was touched. */
export default function EvidenceCard({ partyId, caseId, view, onChange }: { partyId: string; caseId?: string; view: EvidenceView; onChange: () => void }) {
  const [msg, setMsg] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [kind, setKind] = useState(view.types[0]?.code ?? '')
  const [title, setTitle] = useState('')
  const [expires, setExpires] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const needsExpiry = view.types.find(t => t.code === kind)?.expires === 'yes'
  const docs = caseId ? view.documents.filter(d => d.case_id === caseId || d.case_id === null) : view.documents
  const upload = async () => {
    if (!file) return
    try {
      if (file.size > view.max_bytes) throw new Error(`The file is larger than ${size(view.max_bytes)}`)
      await api.post(`/customers/${partyId}/evidence`, { kind, title, filename: file.name, media_type: file.type || 'application/octet-stream', content_base64: await toBase64(file), expires: expires || null, case_id: caseId ?? null })
      setAdding(false); setFile(null); setTitle(''); setExpires(''); setMsg('Stored. Its fingerprint is on record.'); onChange()
    } catch (e) { setMsg((e as Error).message) }
  }
  return (
    <Card title="Documents" action={view.can_change && !adding ? <button onClick={() => setAdding(true)}>Add a document…</button> : undefined}>
      {adding && <div className="override-form">
        <div className="inline wrap">
          <select aria-label="Document type" value={kind} onChange={e => setKind(e.target.value)}>{view.types.map(t => <option key={t.code} value={t.code}>{t.label}</option>)}</select>
          <input type="search" aria-label="Title" placeholder="Title (optional)" value={title} onChange={e => setTitle(e.target.value)} />
          {needsExpiry && <label className="check">Expires <input type="date" value={expires} onChange={e => setExpires(e.target.value)} /></label>}
          <input type="file" aria-label="File" onChange={e => setFile(e.target.files?.[0] ?? null)} />
        </div>
        <div className="inline"><button className="primary" disabled={!file || (needsExpiry && !expires)} onClick={upload}>Store</button><button onClick={() => setAdding(false)}>Cancel</button>
          <span className="muted small">Up to {size(view.max_bytes)}.{needsExpiry ? ' This type of document expires, so it needs the date.' : ''}</span></div>
      </div>}
      {docs.length === 0 ? <p className="empty">No documents on record.</p> : (
        <table className="docs"><thead><tr><th>Document</th><th>Standing</th><th>Added</th><th /></tr></thead>
          <tbody>{docs.map(d => <DocRow key={d.id} d={d} view={view} onChange={onChange} say={setMsg} />)}</tbody></table>)}
      {view.hidden > 0 && <p className="muted small">{view.hidden} more {view.hidden === 1 ? 'document is' : 'documents are'} filed on cases you cannot see.</p>}
      {msg && <p className="msg" role="status">{msg}</p>}
    </Card>
  )
}

/** What happened to this customer between reviews, and what the platform did about each event. */
export function Triggers({ triggers }: { triggers: Trigger[] }) {
  return (
    <Card title="Events since the last review">
      {triggers.length === 0 ? <p className="empty">Nothing has happened that calls for a review.</p> : (
        <ul className="feed">{triggers.map(t => <li key={t.id}>
          <b>{t.label}</b> <span className="muted small">{fmtDate(t.at)}</span>
          <div>{t.detail}</div>
          <div className="small"><Pill tone={t.outcome === 'noted' ? 'neutral' : 'human'}>{OUTCOME[t.outcome] ?? words(t.outcome)}</Pill>{t.case_id && <Link to={`/cases/${t.case_id}`}>{t.case_id}</Link>}</div>
        </li>)}</ul>)}
    </Card>
  )
}
