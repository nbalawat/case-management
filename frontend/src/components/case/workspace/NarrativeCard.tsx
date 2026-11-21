import { useState } from 'react'
import { api } from '../../../api'
import { useNameOf } from '../../../shell/people'
import { Card } from '../../ui'
import type { Act, CaseDetail } from './types'

/** The case narrative: the agent's draft until a person saves it under their own name. Keyed by the saved text, so a save or reload starts afresh. */
export default function NarrativeCard({ d, act }: { d: CaseDetail; act: Act }) {
  const { case: c, access } = d
  const nameOf = useNameOf()
  const [text, setText] = useState(c.narrative)
  const editable = c.open && access.can_work
  const dirty = text !== c.narrative
  return (
    <Card title="Narrative" action={<span className="muted small">{dirty ? 'Unsaved changes' : c.drafted_by ? `Saved by ${nameOf(c.drafted_by)}` : text ? 'Agent draft, not yet reviewed' : ''}</span>}>
      <textarea rows={16} value={text} onChange={e => setText(e.target.value)} disabled={!editable} aria-label="Narrative" className="narrative" />
      {editable && <div className="inline">
        <button className="primary" onClick={() => act(() => api.post(`/cases/${c.id}/narrative`, { text }), 'Narrative saved under your name.')} disabled={!text.trim()}>Save as my narrative</button>
        {dirty && <button className="ghost" onClick={() => setText(c.narrative)}>Discard changes</button>}
        <span className="muted small">{text.trim().split(/\s+/).filter(Boolean).length} words</span>
      </div>}
    </Card>
  )
}
