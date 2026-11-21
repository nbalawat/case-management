import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { ReactFlowProvider } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AlertTriangle, ArrowLeft, CheckCircle2, ChevronDown, CircleAlert, Cloud, CloudOff, Download, FlaskConical, Loader2, Redo2, Rocket, SlidersHorizontal, Undo2 } from 'lucide-react'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '../../components/kit/dropdown-menu'
import { cn } from '../../lib/cn'
import { usePeople } from '../../shell/people'
import { composerApi, saveFile } from './api'
import Canvas from './Canvas'
import { addBlock, attachAction, duplicate, kindOf, removeEdges, removeNodes } from './model'
import { PublishDialog } from './panels/Dialogs'
import Inspector, { type Selection } from './panels/Inspector'
import Palette from './panels/Palette'
import TrialPanel from './panels/TrialPanel'
import { useDesigner, type SaveState } from './useDesigner'
import type { Catalog, Draft, Issue, Preset } from './types'

const SAVE: Record<SaveState, { label: string; icon: typeof Cloud }> = {
  saved: { label: 'Saved', icon: Cloud }, saving: { label: 'Saving…', icon: Loader2 }, unsaved: { label: 'Unsaved', icon: Cloud },
  conflict: { label: 'Changed elsewhere', icon: CloudOff }, error: { label: 'Not saved', icon: CloudOff },
}

type Tab = 'inspect' | 'try' | 'problems'

function Problems({ issues, onPick }: { issues: Issue[]; onPick: (i: Issue) => void }) {
  if (!issues.length) return <div className="cf-verdict good"><CheckCircle2 size={16} /><div><b>No problems</b><p>Every rule passes. Try it on a sample case, then publish.</p></div></div>
  return (
    <ul className="cf-problems">
      {issues.map((i, k) => (
        <li key={k}><button type="button" className={i.level} onClick={() => onPick(i)}>
          {i.level === 'error' ? <CircleAlert size={14} /> : <AlertTriangle size={14} />}
          <span><b>{i.message}</b>{i.hint && <small>{i.hint}</small>}</span>
        </button></li>
      ))}
    </ul>
  )
}

function Workbench({ cat, draft, notes }: { cat: Catalog; draft: Draft; notes: string[] }) {
  const { can, holder } = usePeople()
  const canEdit = can('publish_workflow')
  const dz = useDesigner(draft, canEdit)
  const [selection, setSelection] = useState<Selection>(null)
  const [tab, setTab] = useState<Tab>('inspect')
  const [notice, setNotice] = useState<string | null>(null)
  const [trial, setTrial] = useState<{ current: string | null; visited: string[] }>({ current: null, visited: [] })
  const [publishing, setPublishing] = useState(false)
  const [assumed, setAssumed] = useState(notes)
  const design = dz.design
  const issues = dz.check?.issues ?? []

  useEffect(() => { if (!notice) return; const t = setTimeout(() => setNotice(null), 4200); return () => clearTimeout(t) }, [notice])
  const select = useCallback((s: Selection) => { setSelection(s); if (s) setTab('inspect') }, [])

  const add = (type: string, preset: Preset) => {
    const anchor = selection?.kind === 'node' ? design.nodes.find(n => n.id === selection.id) : undefined
    const bottom = Math.max(0, ...design.nodes.map(n => n.position.y))
    const pos = anchor ? { x: anchor.position.x, y: anchor.position.y + 150 } : { x: 40, y: bottom + 150 }
    const r = addBlock(design, cat, type, preset, pos)
    if (r.id) { dz.change(r.design); select({ kind: 'node', id: r.id }) } else setNotice('This workflow already has its start.')
  }
  const attach = (actionId: string) => {
    if (selection?.kind !== 'node') { setNotice('Select a stage first, or drag the action onto one.'); return }
    const r = attachAction(design, cat, selection.id, actionId)
    if (r.why) setNotice(r.why); else dz.change(r.design)
  }
  const remove = () => {
    if (!selection) return
    dz.change(selection.kind === 'node' ? removeNodes(design, [selection.id]) : removeEdges(design, [selection.id]))
    setSelection(null)
  }
  const dup = () => {
    if (selection?.kind !== 'node') return
    const r = duplicate(design, [selection.id])
    dz.change(r.design)
    if (r.ids[0]) select({ kind: 'node', id: r.ids[0] })
  }
  const exportAs = (fmt: string) => composerApi.export(design, fmt).then(r => saveFile(r.filename, r.content, r.media_type)).catch(e => setNotice(e.message))
  const pick = (i: Issue) => { if (i.id && (i.target === 'node' || i.target === 'edge')) select({ kind: i.target, id: i.id }); else select(null) }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = (e.target as HTMLElement)?.closest?.('input, textarea, select, [contenteditable]')
      const mod = e.metaKey || e.ctrlKey
      if (mod && e.key.toLowerCase() === 's') { e.preventDefault(); dz.flush() }
      if (typing) return
      if (mod && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) dz.redo(); else dz.undo() }
      if (mod && e.key.toLowerCase() === 'y') { e.preventDefault(); dz.redo() }
      if (mod && e.key.toLowerCase() === 'd' && canEdit) { e.preventDefault(); dup() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const errors = dz.check?.errors ?? 0, warnings = dz.check?.warnings ?? 0
  const Save = SAVE[dz.save]
  const counts = useMemo(() => ({
    stages: design.nodes.filter(n => kindOf(cat, n) === 'stage').length,
    outcomes: design.nodes.filter(n => kindOf(cat, n) === 'outcome').length,
  }), [design.nodes, cat])

  return (
    <div className="cf-shell">
      <header className="cf-bar">
        <Link to="/workflows/designer" className="cf-back" aria-label="All drafts"><ArrowLeft size={16} /></Link>
        <div className="cf-title">
          <input aria-label="Workflow name" value={design.title} disabled={!canEdit} onChange={e => dz.change({ ...design, title: e.target.value })} />
          <span className="muted small">{design.key} · v{design.version} · {counts.stages} stage{counts.stages === 1 ? '' : 's'}, {counts.outcomes} outcome{counts.outcomes === 1 ? '' : 's'}
            {dz.status === 'published' && <> · <b className="good">published</b></>}</span>
        </div>
        <span className={cn('cf-save', dz.save)} title={dz.saveError ?? ''}><Save.icon size={14} className={dz.save === 'saving' ? 'spin' : ''} />{Save.label}</span>
        <button type="button" className={cn('cf-status', errors ? 'err' : warnings ? 'warn' : 'ok')} onClick={() => setTab('problems')}>
          {errors ? <CircleAlert size={14} /> : warnings ? <AlertTriangle size={14} /> : <CheckCircle2 size={14} />}
          {errors ? `${errors} problem${errors === 1 ? '' : 's'}` : warnings ? `Ready · ${warnings} warning${warnings === 1 ? '' : 's'}` : dz.check ? 'Ready to publish' : 'Checking…'}
        </button>
        <div className="cf-bar-actions">
          <button type="button" className="ghost" aria-label="Undo" title="Undo (Ctrl+Z)" disabled={!dz.canUndo || !canEdit} onClick={dz.undo}><Undo2 size={16} /></button>
          <button type="button" className="ghost" aria-label="Redo" title="Redo (Ctrl+Shift+Z)" disabled={!dz.canRedo || !canEdit} onClick={dz.redo}><Redo2 size={16} /></button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild><button type="button"><Download size={14} />Export<ChevronDown size={14} /></button></DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {cat.formats.map(f => <DropdownMenuItem key={f.id} onSelect={() => exportAs(f.id)}><div><b>{f.label}</b><div className="muted small">{f.description}</div></div></DropdownMenuItem>)}
            </DropdownMenuContent>
          </DropdownMenu>
          <button type="button" onClick={() => setTab('try')} aria-pressed={tab === 'try'}><FlaskConical size={14} />Try it</button>
          <button type="button" className="primary" disabled={!canEdit || !dz.check?.ok} onClick={async () => { await dz.flush(); setPublishing(true) }}
            title={!canEdit ? `Publishing needs the publish workflow permission (${holder('publish_workflow')})` : !dz.check?.ok ? 'Fix the problems first' : ''}><Rocket size={14} />Publish</button>
        </div>
      </header>
      {!canEdit && <div className="cf-readonly">You can look but not change this. Sign in as {holder('publish_workflow')} to edit and publish workflows.</div>}
      {dz.save === 'conflict' && <div className="cf-readonly bad">{dz.saveError} <button className="ghost" onClick={() => window.location.reload()}>Reload</button></div>}
      {assumed.length > 0 && (
        <div className="cf-assumed" role="status">
          <b>Imported. What we assumed or could not bring in:</b>
          <ul>{assumed.map(n => <li key={n}>{n}</li>)}</ul>
          <button className="ghost" onClick={() => setAssumed([])}>Got it</button>
        </div>
      )}
      <div className="cf-body">
        <Palette cat={cat} readOnly={!canEdit} hasStart={design.nodes.some(n => n.type === 'start')} canAttach={selection?.kind === 'node'} onAdd={add} onAttach={attach} />
        <Canvas cat={cat} design={design} issues={issues} selection={selection} readOnly={!canEdit} trial={trial}
          onChange={dz.change} onSelect={select} onNotice={setNotice} />
        <div className="cf-side">
          <div className="seg cf-tabs" role="tablist" aria-label="Side panel">
            <button role="tab" aria-pressed={tab === 'inspect'} aria-selected={tab === 'inspect'} onClick={() => setTab('inspect')}><SlidersHorizontal size={14} />Inspect</button>
            <button role="tab" aria-pressed={tab === 'try'} aria-selected={tab === 'try'} onClick={() => setTab('try')}><FlaskConical size={14} />Try it</button>
            <button role="tab" aria-pressed={tab === 'problems'} aria-selected={tab === 'problems'} onClick={() => setTab('problems')}>Problems{issues.length > 0 && <span className={cn('tab-count', errors && 'warn')}>{issues.length}</span>}</button>
          </div>
          <div className="cf-side-body">
            {tab === 'inspect' && <Inspector cat={cat} design={design} selection={selection} issues={issues} readOnly={!canEdit}
              onChange={dz.change} onDelete={remove} onDuplicate={dup} onSelect={select} />}
            {tab === 'try' && <TrialPanel design={design} onHighlight={(current, visited) => setTrial({ current, visited })} />}
            {tab === 'problems' && <Problems issues={issues} onPick={pick} />}
          </div>
        </div>
      </div>
      {notice && <div className="cf-toast" role="status">{notice}</div>}
      <PublishDialog open={publishing} onOpenChange={setPublishing} check={dz.check} design={design} draftId={draft.id} saving={dz.save === 'saving' || dz.save === 'unsaved'} onPublished={() => dz.setStatus('published')} />
    </div>
  )
}

/** The workflow designer for one draft. */
export default function Designer({ draftId, notes = [] }: { draftId: string; notes?: string[] }) {
  const [cat, setCat] = useState<Catalog | null>(null)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    Promise.all([composerApi.catalog(), composerApi.draft(draftId)]).then(([c, d]) => { setCat(c); setDraft(d) }).catch(e => setError(e.message))
  }, [draftId])
  if (error) return <div className="loading" role="alert">{error}</div>
  if (!cat || !draft) return <div className="loading">Opening the designer…</div>
  return <ReactFlowProvider><Workbench cat={cat} draft={draft} notes={notes} /></ReactFlowProvider>
}
