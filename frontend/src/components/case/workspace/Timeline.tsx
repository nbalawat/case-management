import { useState } from 'react'
import { fmtDateTime, words } from '../../../api'
import { useNameOf } from '../../../shell/people'
import { Card, Pill } from '../../ui'
import { Segmented } from '../../grid/controls'
import type { CaseDetail } from './types'

const KINDS = ['all', 'human', 'agent', 'system'] as const
const LABEL: Record<string, string> = { all: 'Everyone', human: 'People', agent: 'Agents', system: 'System' }

/** The audit trail, newest first, filterable by who acted. */
export default function Timeline({ events }: { events: CaseDetail['events'] }) {
  const nameOf = useNameOf()
  const [kind, setKind] = useState<(typeof KINDS)[number]>('all')
  const count = (k: string) => (k === 'all' ? events.length : events.filter(e => e.actor_kind === k).length)
  const shown = kind === 'all' ? events : events.filter(e => e.actor_kind === kind)
  return (
    <Card title="Audit trail" action={<Segmented label="Who acted" value={kind} onChange={v => setKind(v as typeof kind)}
      options={KINDS.map(k => [k, `${LABEL[k]} ${count(k)}`])} />}>
      {shown.length === 0 ? <p className="empty">Nothing recorded.</p> :
        <ul className="events tight">{shown.map(e => (
          <li key={e.id}><Pill tone={e.actor_kind === 'agent' ? 'agent' : e.actor_kind === 'human' ? 'human' : 'neutral'}>{e.actor_kind}</Pill>
            <span><b>{words(e.kind.replace('.', ' '))}</b> · {nameOf(e.actor)}{typeof e.payload.note === 'string' && e.payload.note ? <em> “{e.payload.note}”</em> : null}</span>
            <time>{fmtDateTime(e.ts)}</time></li>))}</ul>}
    </Card>
  )
}
