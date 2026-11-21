import { useState } from 'react'
import { api, fmtDateTime, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../shell/people'
import { Card, Loading, Pill } from '../components/ui'

type Kind = 'list' | 'number' | 'text'
type Code = { code: string; label: string; retired?: boolean } & Record<string, unknown>
type List = { id: string; title: string; about: string; fields: Record<string, Kind>; own: boolean; codes: Code[]; updated_by: string | null; updated_at: string | null }

const show = (v: unknown, kind: Kind) => (v === undefined || v === null ? '' : kind === 'list' ? (v as string[]).join(', ') : String(v))
const parse = (text: string, kind: Kind): unknown => (kind === 'list' ? text.split(',').map(x => x.trim()).filter(Boolean) : kind === 'number' ? (text === '' ? undefined : Number(text)) : text)

export default function Reference() {
  const { data, error, reload } = useApi<{ can_edit: boolean; lists: List[] }>('/reference')
  const { holder } = usePeople()
  if (!data) return <Loading error={error} />
  return (
    <>
      <div className="page-h"><h1>Lists</h1><p>The choices this client's people pick from: why a case closed, what QA scores and why work fell short, and which weak alerts may wait.
        {!data.can_edit && <> You can read these lists. {holder('publish_workflow')} can change them.</>}</p></div>
      {data.lists.map(l => <ListCard key={l.id} list={l} canEdit={data.can_edit} onSaved={reload} />)}
    </>
  )
}

function ListCard({ list, canEdit, onSaved }: { list: List; canEdit: boolean; onSaved: () => void }) {
  const [rows, setRows] = useState<Code[]>(list.codes)
  const [msg, setMsg] = useState<string | null>(null)
  const nameOf = useNameOf()
  const [from, setFrom] = useState(list.codes)
  if (from !== list.codes) { setFrom(list.codes); setRows(list.codes) }  // a saved list comes back: start again from it
  const fields = Object.entries(list.fields)
  const known = new Set(list.codes.map(c => c.code))
  const dirty = JSON.stringify(rows) !== JSON.stringify(list.codes)
  const set = (i: number, change: Partial<Code>) => setRows(r => r.map((c, j) => (j === i ? { ...c, ...change } : c)))
  const save = () => api.put(`/reference/${list.id}`, { codes: rows.map(c => Object.fromEntries(Object.entries(c).filter(([, v]) => v !== undefined && v !== ''))) })
    .then(() => { setMsg('Saved. It applies to the next decision anyone makes.'); onSaved() }).catch(e => setMsg(e.message))
  return (
    <Card title={list.title} action={<Pill tone={list.own ? 'human' : 'neutral'}>{list.own ? `This client's own list · ${nameOf(list.updated_by)}, ${fmtDateTime(list.updated_at!)}` : 'Standard list'}</Pill>}>
      <p className="muted small">{list.about}</p>
      {rows.length === 0 && <p className="empty">Nothing here yet.</p>}
      {rows.length > 0 && <div className="scroll"><table className="codes">
        <thead><tr><th>Code</th><th>Shown to people as</th>{fields.map(([f]) => <th key={f}>{words(f)}</th>)}<th>In use</th>{canEdit && <th />}</tr></thead>
        <tbody>{rows.map((c, i) => (
          <tr key={i} className={c.retired ? 'retired' : ''}>
            <td>{known.has(c.code) ? <code>{c.code}</code> : <input aria-label="Code" value={c.code} placeholder="short_id" onChange={e => set(i, { code: e.target.value })} />}</td>
            <td>{canEdit ? <input aria-label={`Label for ${c.code}`} value={c.label} onChange={e => set(i, { label: e.target.value })} /> : c.label}</td>
            {fields.map(([f, kind]) => <td key={f}>{canEdit
              ? <input aria-label={`${words(f)} for ${c.code}`} type={kind === 'number' ? 'number' : 'text'} step="any" value={show(c[f], kind)} onChange={e => set(i, { [f]: parse(e.target.value, kind) })} />
              : show(c[f], kind)}</td>)}
            <td>{canEdit ? <input type="checkbox" aria-label={`${c.code} in use`} checked={!c.retired} onChange={e => set(i, { retired: e.target.checked ? undefined : true })} /> : c.retired ? 'Retired' : 'Yes'}</td>
            {canEdit && <td>{!known.has(c.code) && <button className="ghost" onClick={() => setRows(r => r.filter((_, j) => j !== i))}>Remove</button>}</td>}
          </tr>))}</tbody>
      </table></div>}
      {canEdit && <div className="inline">
        <button onClick={() => setRows(r => [...r, { code: '', label: '' }])}>Add a code</button>
        <button className="primary" disabled={!dirty} onClick={save}>Save list</button>
        {dirty && <button className="ghost" onClick={() => { setRows(list.codes); setMsg(null) }}>Discard changes</button>}
        <span className="muted small">A code already used on records is retired, not deleted, so history still reads.</span>
      </div>}
      {msg && <p className="msg" role="status">{msg}</p>}
    </Card>
  )
}
