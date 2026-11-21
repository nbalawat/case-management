import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Archive, ArchiveRestore, FileUp, LayoutTemplate, PencilRuler, Sparkles, Workflow } from 'lucide-react'
import { fmtDateTime } from '../../api'
import { Pill } from '../../components/ui'
import { useNameOf, usePeople } from '../../shell/people'
import { composerApi } from './api'
import { ImportDialog } from './panels/Dialogs'
import type { Design, DraftSummary, Template } from './types'

/** Where composing starts: pick a template, bring a file, or reopen a live workflow; and every draft in progress. */
export default function Home() {
  const nav = useNavigate()
  const nameOf = useNameOf()
  const { can, holder } = usePeople()
  const canEdit = can('publish_workflow')
  const [templates, setTemplates] = useState<Template[] | null>(null)
  const [drafts, setDrafts] = useState<DraftSummary[] | null>(null)
  const [archived, setArchived] = useState(false)
  const [importing, setImporting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = () => composerApi.drafts(archived).then(setDrafts).catch(e => setError(e.message))
  useEffect(() => { composerApi.templates().then(setTemplates).catch(e => setError(e.message)) }, [])
  useEffect(() => { load() }, [archived]) // eslint-disable-line react-hooks/exhaustive-deps

  const start = (template: string) => {
    setBusy(template)
    composerApi.create({ template }).then(d => nav(`/workflows/designer/${d.id}`)).catch(e => { setError(e.message); setBusy(null) })
  }
  const imported = (design: Design, notes: string[], origin: string) =>
    composerApi.create({ design, origin }).then(d => nav(`/workflows/designer/${d.id}`, { state: { notes } })).catch(e => setError(e.message))

  const fresh = templates?.filter(t => t.kind === 'template') ?? []
  const live = templates?.filter(t => t.kind === 'case_type') ?? []
  return (
    <>
      <div className="page-h cf-home-h">
        <div>
          <h1>Workflow designer</h1>
          <p>Compose a workflow from ready blocks: stages, approvals, decisions, automatic actions and outcomes. It checks itself as you go,
            you can try it on a sample case, and publishing makes it live for this client as a new version. Both workflow engines run it unchanged.</p>
        </div>
        <Link to="/workflows" className="cf-link-btn"><Workflow size={15} />Live workflows</Link>
      </div>
      {!canEdit && <div className="cf-readonly">You can browse drafts. Creating and publishing workflows needs {holder('publish_workflow')}.</div>}
      {error && <p className="cf-error" role="alert">{error}</p>}

      <div className="cf-start">
        <section className="card cf-start-col wide">
          <div className="card-h"><h2><LayoutTemplate size={16} /> Start from a template</h2><span className="muted small">fitted to this client's queues and people</span></div>
          <div className="cf-gallery">
            {fresh.map(t => (
              <button key={t.id} type="button" className="cf-tpl" disabled={!canEdit || !!busy} onClick={() => start(t.id)}>
                <span className="cf-tpl-domain">{t.domain}</span>
                <b>{t.title}</b>
                <span>{t.summary}</span>
                <span className="cf-tpl-tags">{t.tags.map(x => <Pill key={x}>{x}</Pill>)}</span>
                {busy === t.id && <span className="muted small">Opening…</span>}
              </button>
            ))}
          </div>
        </section>
        <div className="cf-start-col">
          <section className="card">
            <div className="card-h"><h2><FileUp size={16} /> Bring your own</h2></div>
            <p className="muted">Import BPMN 2.0 from Camunda, Flowable, Signavio or bpmn.io, a case-type YAML, or a CaseForge export. You will see what was assumed.</p>
            <button className="primary" disabled={!canEdit} onClick={() => setImporting(true)}><FileUp size={14} />Import a file</button>
          </section>
          <section className="card">
            <div className="card-h"><h2><PencilRuler size={16} /> Change a live workflow</h2></div>
            <ul className="cf-live">
              {live.map(t => (
                <li key={t.id}><button type="button" className="ghost" disabled={!canEdit || !!busy} onClick={() => start(t.id)}>
                  <b>{t.title}</b><span className="muted small">{t.summary.split('.')[0].replace('Your live ', '')}</span></button></li>
              ))}
            </ul>
          </section>
        </div>
      </div>

      <section className="card">
        <div className="card-h"><h2><Sparkles size={16} /> Drafts</h2>
          <label className="check"><input type="checkbox" checked={archived} onChange={e => setArchived(e.target.checked)} />Show archived</label></div>
        {!drafts ? <p className="muted">Loading…</p> : drafts.length === 0 ? <p className="empty">No drafts yet. Pick a template above to start one.</p> : (
          <table className="rows">
            <thead><tr><th>Workflow</th><th>Status</th><th>Version</th><th className="num">Blocks</th><th>Started from</th><th>Last change</th><th /></tr></thead>
            <tbody>{drafts.map(d => (
              <tr key={d.id} tabIndex={0} onClick={() => nav(`/workflows/designer/${d.id}`)} onKeyDown={e => { if (e.key === 'Enter') nav(`/workflows/designer/${d.id}`) }}>
                <td><b>{d.title}</b><div className="muted small">{d.case_type}</div></td>
                <td>{d.status === 'published' ? <Pill tone="good">published v{d.published_version}</Pill> : d.status === 'archived' ? <Pill>archived</Pill> : <Pill tone="human">draft</Pill>}</td>
                <td>v{d.version}</td>
                <td className="num">{d.blocks}</td>
                <td className="small">{d.origin}</td>
                <td className="small">{fmtDateTime(d.updated)} by {nameOf(d.updated_by)}</td>
                <td>{canEdit && <button type="button" className="ghost" aria-label={d.status === 'archived' ? 'Restore' : 'Archive'}
                  onClick={e => { e.stopPropagation(); composerApi.archive(d.id, d.status === 'archived').then(load) }}>
                  {d.status === 'archived' ? <ArchiveRestore size={15} /> : <Archive size={15} />}</button>}</td>
              </tr>))}</tbody>
          </table>
        )}
      </section>
      <ImportDialog open={importing} onOpenChange={setImporting} onImported={imported} />
    </>
  )
}
