import { useRef, useState } from 'react'
import { Download, Upload } from 'lucide-react'
import { api, fmtDateTime, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../shell/people'
import { Card, Loading, Pill } from '../components/ui'
import CheckPanel from '../components/decisions/CheckPanel'
import DecisionGrid from '../components/decisions/DecisionGrid'
import TryPanel from '../components/decisions/TryPanel'
import { HIT_HELP, nextVersion, toDraft, type Check, type Detail, type Reference, type Summary, type Table } from '../components/decisions/types'

function flags(check: Check | null, matched: string[]): Record<string, string> {
  const out: Record<string, string> = Object.fromEntries(matched.map(id => [id, 'hit']))
  const a = check?.analysis
  if (!a) return out
  for (const id of [...a.never_match, ...a.never_decide]) out[id] = 'dead'
  for (const e of a.errors) for (const m of e.matchAll(/Rules (\S+) and (\S+)/g)) { out[m[1]] = 'bad'; out[m[2]] = 'bad' }
  return out
}

/** One table: edit as a grid, try it, check it against recent cases, publish, exchange as DMN. Keyed by table and version. */
function Editor({ detail, reference, onPublished }: { detail: Detail; reference: Reference; onPublished: (msg: string) => void }) {
  const { can, holder } = usePeople()
  const nameOf = useNameOf()
  const admin = can('publish_workflow')
  const [table, setTable] = useState<Table>(detail)
  const [check, setCheck] = useState<Check | null>(null)
  const [matched, setMatched] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [showYaml, setShowYaml] = useState(false)
  const file = useRef<HTMLInputElement>(null)
  const edited = JSON.stringify(toDraft(table)) !== JSON.stringify(toDraft(detail))
  const change = (t: Table) => { setTable(t); setCheck(null); setMatched([]) }
  const startDraft = () => change({ ...table, version: nextVersion(detail.version) })
  const run = () => api.post<Check>('/decisions/check', { table: toDraft(table) }).then(c => { setCheck(c); setErr(null) }).catch(e => setErr(e.message))
  const publish = () => api.post<{ version: string }>('/decisions/publish', { table: toDraft(table), note })
    .then(r => onPublished(`Published v${r.version}. ${table.enabled && table.binding !== 'none' ? 'It applies to cases that open from now on.' : 'It is not in use until enabled.'}`))
    .catch(e => setErr(e.message))
  const importDmn = (f: File) => f.text().then(xml => api.post<Check>('/decisions/check', { dmn: xml }))
    .then(c => { if (c.ok && c.table) { setTable(c.table); setCheck(c); setErr(null) } else setErr(c.error ?? 'Could not read the file') })
    .catch(e => setErr(e.message))
  const facts = reference.contexts[table.context] ?? []
  const binding = reference.bindings.find(b => b.id === table.binding)
  const drafting = table.version !== detail.version
  return (
    <>
      <Card title={`${table.title} · v${table.version}${drafting ? ' (draft)' : ''}`} action={
        <span className="toolbar dt-tools">
          <button onClick={() => api.download(`/decisions/${detail.id}/dmn?version=${detail.version}`)} title="DMN 1.3 XML, for Camunda, Kogito and other DMN tools"><Download size={14} /> DMN</button>
          {admin && <><button onClick={() => file.current?.click()}><Upload size={14} /> Import DMN</button>
            <input ref={file} type="file" accept=".dmn,.xml" hidden onChange={e => e.target.files?.[0] && importDmn(e.target.files[0])} /></>}
          <button aria-pressed={showYaml} onClick={() => setShowYaml(!showYaml)}>YAML</button>
        </span>}>
        <p className="muted">{table.description}</p>
        <div className="dt-settings">
          <label><span>Hit policy</span>
            <select value={table.hit_policy} disabled={!drafting} onChange={e => change({ ...table, hit_policy: e.target.value })}>
              {reference.hit_policies.map(h => <option key={h}>{h}</option>)}</select></label>
          {table.hit_policy === 'COLLECT' && <label><span>Aggregation</span>
            <select value={table.aggregation ?? ''} disabled={!drafting} onChange={e => change({ ...table, aggregation: e.target.value || null })}>
              <option value="">none (a list)</option>{reference.aggregations.map(a => <option key={a}>{a}</option>)}</select></label>}
          <label><span>Used for</span><b>{binding?.label ?? table.binding}</b></label>
          <label><span>Reads</span><b>{words(table.context)} facts</b></label>
          {binding && binding.id !== 'none' && (
            <label className="dt-switch"><input type="checkbox" checked={table.enabled} disabled={!drafting} onChange={e => change({ ...table, enabled: e.target.checked })} />
              <span>{table.enabled ? 'In use' : 'Not in use'}</span></label>
          )}
          {table.case_types.length > 0 && <label><span>Case types</span><b>{table.case_types.map(words).join(', ')}</b></label>}
        </div>
        <p className="small muted">{HIT_HELP[table.hit_policy]}</p>
        {showYaml ? <pre className="dt-yaml">{check?.yaml ?? detail.yaml}</pre> :
          <DecisionGrid table={table} facts={facts} readOnly={!drafting || !admin} flagged={flags(check, matched)} onChange={change} />}
        <div className="toolbar">
          {!drafting && admin && <button className="primary" onClick={startDraft}>Edit as v{nextVersion(detail.version)}</button>}
          {!admin && <span className="muted small">Sign in as {holder('publish_workflow')} to change decision tables.</span>}
          {drafting && <>
            <button onClick={run}>Check draft</button>
            <input type="search" placeholder="What changed and why" value={note} onChange={e => setNote(e.target.value)} aria-label="Change note" />
            <button className="primary" disabled={!check?.ok || !check.publishable || !admin} onClick={publish}
              title={!check?.ok ? 'Check the draft first' : !check.publishable ? 'Fix the errors first' : ''}>Publish v{table.version}</button>
            <button onClick={() => change(detail)}>Discard</button>
            {edited && !check && <span className="muted small">Unchecked changes</span>}
          </>}
        </div>
        {err && <p className="msg" role="alert">{err}</p>}
        {check && <CheckPanel check={check} />}
      </Card>
      <div className="cols-2">
        <TryPanel table={table} onMatched={setMatched} />
        <Card title="Versions">
          <table><thead><tr><th>Version</th><th>Source</th><th>State</th><th>Published</th></tr></thead>
            <tbody>{detail.versions.map(v => (
              <tr key={v.version}><td><b>v{v.version}</b> {v.active && <Pill tone="good">active</Pill>}</td><td>{v.origin}</td>
                <td>{v.enabled ? <Pill tone="good">in use</Pill> : <Pill>not in use</Pill>}</td>
                <td className="small">{v.published ? `${fmtDateTime(v.published)} by ${nameOf(v.published_by ?? '')}${v.note ? `: ${v.note}` : ''}` : 'Platform library'}</td></tr>))}</tbody></table>
          <h3>Facts this table can read</h3>
          <ul className="plain small">{facts.map(f => <li key={f.name}><code>{f.name}</code> {f.label} <span className="muted">({f.kind})</span></li>)}</ul>
        </Card>
      </div>
    </>
  )
}

export default function Decisions() {
  const list = useApi<Summary[]>('/decisions')
  const reference = useApi<Reference>('/decisions/reference')
  const [sel, setSel] = useState<string | null>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const current = sel ?? list.data?.[0]?.id ?? null
  const detail = useApi<Detail>(current ? `/decisions/${current}` : null)
  if (!list.data || !reference.data) return <Loading error={list.error ?? reference.error} />
  return (
    <>
      <div className="page-h"><h1>Decision tables</h1>
        <p>Business decisions written as tables of rules (DMN): which priority a case opens with, which team triages it, a customer's risk band.
          Each table is checked for overlapping rules and inputs nothing covers, replayed on recent cases, and versioned per client. Workflows and agents can also call any table by name.</p></div>
      <div className="dt-list" role="list">
        {list.data.map(t => (
          <button key={t.id} role="listitem" aria-pressed={t.id === current} onClick={() => { setSel(t.id); setMsg(null) }}>
            <b>{t.title}</b>
            <span className="small muted">{t.binding_label} · {t.rules} rules · {t.hit_policy}</span>
            <span>{t.enabled ? <Pill tone="good">in use</Pill> : t.binding === 'none' ? <Pill>on call</Pill> : <Pill>not in use</Pill>} <span className="muted small">v{t.version}</span></span>
          </button>
        ))}
      </div>
      {msg && <p className="verdict good" role="status">{msg}</p>}
      {!detail.data || detail.data.id !== current ? <Loading error={detail.error} /> :
        <Editor key={`${detail.data.id}@${detail.data.version}`} detail={detail.data} reference={reference.data}
          onPublished={m => { setMsg(m); list.reload(); detail.reload() }} />}
    </>
  )
}
