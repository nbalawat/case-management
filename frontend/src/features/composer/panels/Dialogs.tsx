import { useRef, useState } from 'react'
import { CheckCircle2, FileUp, Rocket } from 'lucide-react'
import { Link } from 'react-router-dom'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '../../../components/kit/dialog'
import { composerApi } from '../api'
import type { Check, Design } from '../types'

export function PublishDialog({ open, onOpenChange, check, design, draftId, onPublished, saving }: {
  open: boolean; saving: boolean; onOpenChange: (o: boolean) => void; check: Check | null; design: Design; draftId: string; onPublished: () => void
}) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState<{ version: string; changes: string[] } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const publish = () => {
    setBusy(true)
    composerApi.publish(draftId, note).then(r => { setDone(r); setError(null); onPublished() }).catch(e => setError(e.message)).finally(() => setBusy(false))
  }
  return (
    <Dialog open={open} onOpenChange={o => { onOpenChange(o); if (!o) { setDone(null); setError(null) } }}>
      <DialogContent className="cf-dialog">
        {done ? <>
          <DialogTitle className="cf-dialog-t"><CheckCircle2 size={18} className="good" />{design.title} v{done.version} is live</DialogTitle>
          <DialogDescription className="cf-help">New cases use it from now on. Cases already open finish on the version they started with. The change is in the client's change register.</DialogDescription>
          <ul className="cf-changes">{done.changes.map(c => <li key={c}>{c}</li>)}</ul>
          <div className="toolbar"><Link className="cf-link-btn" to="/workflows">See it on the Workflows page</Link><button onClick={() => onOpenChange(false)}>Keep designing</button></div>
        </> : <>
          <DialogTitle className="cf-dialog-t"><Rocket size={18} />Publish {design.title} v{design.version}</DialogTitle>
          <DialogDescription className="cf-help">{check?.is_new ? 'A new workflow for this client only.' : 'A new version of a live workflow, for this client only.'}</DialogDescription>
          {check?.changes && <><h4>Publishing will</h4><ul className="cf-changes">{check.changes.map(c => <li key={c}>{c}</li>)}</ul></>}
          {!!check?.warnings && <p className="cf-help">{check.warnings} warning{check.warnings === 1 ? '' : 's'} worth a look first; none stop publishing.</p>}
          <label className="cf-field">What changed and why<textarea rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="Recorded with the version" /></label>
          {check?.yaml && <details><summary>The definition the engines will run</summary><pre className="cf-yaml">{check.yaml}</pre></details>}
          {error && <p className="cf-error" role="alert">{error}</p>}
          <div className="toolbar"><button className="primary" disabled={busy || saving || !check?.ok} onClick={publish}><Rocket size={14} />{busy ? 'Publishing…' : saving ? 'Saving…' : 'Publish'}</button><button onClick={() => onOpenChange(false)}>Cancel</button></div>
        </>}
      </DialogContent>
    </Dialog>
  )
}

/** Bring a workflow in from a file: CaseForge JSON, case-type YAML, or BPMN 2.0 from another modelling tool. */
export function ImportDialog({ open, onOpenChange, onImported }: { open: boolean; onOpenChange: (o: boolean) => void; onImported: (d: Design, notes: string[], origin: string) => void }) {
  const [text, setText] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const read = (f: File) => { setName(f.name); f.text().then(setText) }
  const go = () => {
    setBusy(true)
    composerApi.import(text, name).then(r => { onImported(r.design, r.notes, name || `pasted ${r.format}`); setText(''); setName(''); setError(null) })
      .catch(e => setError(e.message)).finally(() => setBusy(false))
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="cf-dialog">
        <DialogTitle className="cf-dialog-t"><FileUp size={18} />Bring your own workflow</DialogTitle>
        <DialogDescription className="cf-help">From Camunda, Flowable, Signavio or bpmn.io (BPMN 2.0), a case-type YAML, or a CaseForge export. Lanes become queues,
          gateways become decisions, timers become stage clocks. Anything that needs a decision from you is listed after import.</DialogDescription>
        <div className={`cf-drop ${over ? 'over' : ''}`} onDragOver={e => { e.preventDefault(); setOver(true) }} onDragLeave={() => setOver(false)}
          onDrop={e => { e.preventDefault(); setOver(false); const f = e.dataTransfer.files[0]; if (f) read(f) }} onClick={() => input.current?.click()} role="button" tabIndex={0}
          onKeyDown={e => { if (e.key === 'Enter') input.current?.click() }}>
          <FileUp size={22} /><b>{name || 'Drop a file here, or choose one'}</b><span>.bpmn · .xml · .yaml · .json</span>
          <input ref={input} type="file" accept=".bpmn,.xml,.yaml,.yml,.json" hidden onChange={e => { const f = e.target.files?.[0]; if (f) read(f) }} />
        </div>
        <label className="cf-field">…or paste it<textarea rows={6} className="code" value={text} onChange={e => { setText(e.target.value); setName('') }} spellCheck={false} /></label>
        {error && <p className="cf-error" role="alert">{error}</p>}
        <div className="toolbar"><button className="primary" disabled={!text.trim() || busy} onClick={go}>{busy ? 'Reading…' : 'Open in the designer'}</button><button onClick={() => onOpenChange(false)}>Cancel</button></div>
      </DialogContent>
    </Dialog>
  )
}
