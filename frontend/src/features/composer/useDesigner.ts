import { useCallback, useEffect, useRef, useState } from 'react'
import { composerApi } from './api'
import { record, redo, undo, type History } from './model'
import type { Check, Design, Draft } from './types'

export type SaveState = 'saved' | 'saving' | 'unsaved' | 'conflict' | 'error'

/**
 * The open draft: its design with undo and redo, saved automatically a moment after each change, and checked by the
 * server a moment after that. Saving carries the revision it started from, so two people never overwrite each other unseen.
 */
export function useDesigner(draft: Draft, canEdit: boolean) {
  const [hist, setHist] = useState<History>(() => ({ past: [], present: draft.design, future: [] }))
  const [revision, setRevision] = useState(draft.revision)
  const [save, setSave] = useState<SaveState>('saved')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [check, setCheck] = useState<Check | null>(null)
  const [status, setStatus] = useState<Draft['status']>(draft.status)
  const [saved, setSaved] = useState<Design>(draft.design)
  const inFlight = useRef<Promise<void> | null>(null)

  const design = hist.present
  const change = useCallback((next: Design) => setHist(h => record(h, next)), [])
  const doUndo = useCallback(() => setHist(h => undo(h)), [])
  const doRedo = useCallback(() => setHist(h => redo(h)), [])

  // check on every change
  useEffect(() => {
    const t = setTimeout(() => composerApi.validate(design).then(setCheck).catch(e => setCheck({ ok: false, errors: 1, warnings: 0,
      issues: [{ level: 'error', target: 'workflow', id: null, message: e.message, hint: '' }] })), 350)
    return () => clearTimeout(t)
  }, [design])

  const flush = useCallback(async () => {
    if (inFlight.current) { await inFlight.current; return }  // the save that finished will schedule the next one
    if (!canEdit || design === saved) return
    setSave('saving')
    inFlight.current = composerApi.save(draft.id, design, revision).then(r => {
      setSaved(design)
      setRevision(r.revision)
      setStatus(r.status)
      setSave('saved')
      setSaveError(null)
    }, (e: Error) => {
      setSave(e.message.includes('Reload') ? 'conflict' : 'error')
      setSaveError(e.message)
    }).finally(() => { inFlight.current = null })
    await inFlight.current
  }, [draft, design, canEdit, revision, saved])

  // save a moment after the last change; one save at a time, so each carries the revision the last one returned
  useEffect(() => {
    if (design === saved || !canEdit || save === 'conflict' || save === 'saving') return
    const t = setTimeout(flush, 900)
    return () => clearTimeout(t)
  }, [design, save]) // eslint-disable-line react-hooks/exhaustive-deps

  return {
    design, change, undo: doUndo, redo: doRedo, canUndo: hist.past.length > 0, canRedo: hist.future.length > 0,
    save: save === 'saved' && design !== saved ? 'unsaved' as SaveState : save, saveError, flush, check, status, setStatus,
  }
}
