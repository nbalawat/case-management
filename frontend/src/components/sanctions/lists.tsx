import { useState } from 'react'
import { api, fmtDate, fmtDateTime } from '../../api'
import { useNameOf } from '../../shell/people'
import { Pill } from '../ui'
import { rescreened, type Entry, type List, type Rescreen } from './types'

/** Screening lists: one card per list with its versions and entries, and the form that loads a new version. */


export function ListCard({ l, canManage, onChange }: { l: List; canManage: boolean; onChange: (m: string) => void }) {
  const nameOf = useNameOf()
  const [add, setAdd] = useState<null | { ref: string; name: string; countries: string }>(null)
  const [err, setErr] = useState<string | null>(null)
  const run = (p: Promise<{ rescreen?: Rescreen | null }>, ok: string) => p.then(r => { setAdd(null); setErr(null); onChange(ok + rescreened(r.rescreen ?? null)) }).catch(e => setErr(e.message))
  const toggle = () => { const note = window.prompt(l.active ? 'Why stop screening against this list?' : 'Why screen against this list again?'); if (note) run(api.post(`/sanctions/lists/${l.code}/active`, { active: !l.active, note }), l.active ? 'List switched off.' : 'List switched on.') }
  const remove = (e: Entry) => { const note = window.prompt(`Why remove ${e.name}?`); if (note) run(api.post(`/sanctions/lists/${l.code}/entries/${encodeURIComponent(e.ref)}/remove`, { note }), `${e.name} removed. It stays in the history.`) }
  return (
    <div className="wlist">
      <div className="hold-h"><b>{l.name}</b><Pill tone={l.kind === 'internal' ? 'human' : 'neutral'}>{l.kind === 'internal' ? "The client's own" : 'Published'}</Pill>
        {l.active ? <Pill tone="good">Screening</Pill> : <Pill tone="serious">Switched off</Pill>}
        <span className="muted small">version {l.version} · {l.entries} entries{l.removed ? `, ${l.removed} removed` : ''} · {nameOf(l.updated_by) || l.updated_by}, {fmtDate(l.updated_at)}</span>
        {canManage && <span className="push"><button className="ghost" onClick={toggle}>{l.active ? 'Switch off' : 'Switch on'}</button>
          {l.kind === 'internal' && !add && <button onClick={() => setAdd({ ref: '', name: '', countries: '' })}>Add a name…</button>}</span>}</div>
      {l.source && <div className="small muted">{l.source}</div>}
      {add && <div className="inline"><input type="search" aria-label="Reference" placeholder="Reference" value={add.ref} onChange={e => setAdd({ ...add, ref: e.target.value })} />
        <input type="search" aria-label="Name" placeholder="Name" value={add.name} onChange={e => setAdd({ ...add, name: e.target.value })} />
        <input type="search" aria-label="Countries" placeholder="Countries, e.g. PA; CY" value={add.countries} onChange={e => setAdd({ ...add, countries: e.target.value })} />
        <button className="primary" disabled={!add.ref.trim() || !add.name.trim()} onClick={() => run(api.put(`/sanctions/lists/${l.code}/entries`, { entry: add }), `${add.name} added.`)}>Add and rescreen</button>
        <button onClick={() => setAdd(null)}>Cancel</button></div>}
      {err && <p className="msg" role="alert">{err}</p>}
      <details><summary className="small">Entries and history</summary>
        <div className="scroll tall short"><table><thead><tr><th>Reference</th><th>Name</th><th>Countries</th><th>Programme</th><th>Since</th><th /></tr></thead>
          <tbody>{l.rows.map(e => <tr key={e.id} className={e.removed_version ? 'gone' : ''}>
            <td>{e.ref}</td><td><b>{e.name}</b>{e.aliases.length > 0 && <div className="muted small">also {e.aliases.join(', ')}</div>}</td><td>{e.countries.join(', ')}</td><td>{e.programs.join(', ')}</td>
            <td>v{e.added_version}{e.removed_version ? `, removed in v${e.removed_version}` : ''}</td>
            <td>{canManage && l.kind === 'internal' && !e.removed_version && <button className="ghost" onClick={() => remove(e)}>Remove</button>}</td></tr>)}</tbody></table></div>
        {l.more > 0 && <p className="muted small">And {l.more} more.</p>}
        <ol className="custody">{l.history.map((x, i) => <li key={i}><b>Version {x.version}</b> by {nameOf(x.by) || x.by}, {fmtDateTime(x.at)}: {x.added} added, {x.changed} changed, {x.removed} removed{x.note ? `. ${x.note}` : ''}</li>)}</ol>
      </details>
    </div>
  )
}

export function LoadList({ lists, onChange }: { lists: List[]; onChange: (m: string) => void }) {
  const [code, setCode] = useState(lists.find(l => l.kind === 'sanctions')?.code ?? '')
  const [fresh, setFresh] = useState({ code: '', name: '', kind: 'sanctions' })
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const isNew = code === ''
  const read = (f: File | undefined) => f?.text().then(setText)
  const load = () => api.post<{ version: number; added: string[]; changed: string[]; removed: string[]; exemptions_ended: number; unchanged: boolean; rescreen: Rescreen | null }>(
    '/sanctions/lists', { ...(isNew ? fresh : { code }), text, note })
    .then(r => { setText(''); setNote(''); setErr(null); onChange(r.unchanged ? 'That is the version already held. Nothing changed.'
      : `Version ${r.version} loaded: ${r.added.length} added, ${r.changed.length} changed, ${r.removed.length} removed${r.exemptions_ended ? `, ${r.exemptions_ended} exemption(s) ended` : ''}.${rescreened(r.rescreen)}`) })
    .catch(e => setErr(e.message))
  return (
    <div className="override-form">
      <h3>Load a list version</h3>
      <div className="inline wrap">
        <select aria-label="List" value={code} onChange={e => setCode(e.target.value)}>{lists.map(l => <option key={l.code} value={l.code}>{l.name}</option>)}<option value="">A new list…</option></select>
        {isNew && <><input type="search" aria-label="Code" placeholder="Code, e.g. eu_consolidated" value={fresh.code} onChange={e => setFresh({ ...fresh, code: e.target.value })} />
          <input type="search" aria-label="Name" placeholder="Name" value={fresh.name} onChange={e => setFresh({ ...fresh, name: e.target.value })} />
          <select aria-label="Kind" value={fresh.kind} onChange={e => setFresh({ ...fresh, kind: e.target.value })}><option value="sanctions">Published sanctions list</option><option value="internal">The client's own list</option></select></>}
        <input type="file" aria-label="File" accept=".csv,.json,text/csv,application/json" onChange={e => read(e.target.files?.[0])} />
      </div>
      <textarea rows={4} aria-label="List contents" placeholder={'The list as issued: CSV with a header row (ref,name,aliases,kind,countries,programs,identifiers) or JSON.\nref,name,aliases,countries\nX-1,Orion Freight,Orion; OF Ltd,PA; CY'} value={text} onChange={e => setText(e.target.value)} />
      <div className="inline"><input type="search" aria-label="Note" placeholder="Note, such as the publisher's issue number" value={note} onChange={e => setNote(e.target.value)} />
        <button className="primary" disabled={!text.trim()} onClick={load}>Load and rescreen</button></div>
      <p className="muted small">The load is compared with what is held. Entries no longer on it are marked removed, never deleted, and everyone on the books is screened again.</p>
      {err && <p className="msg" role="alert">{err}</p>}
    </div>
  )
}
