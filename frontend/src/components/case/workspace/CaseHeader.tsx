import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Bot, Clock, Gavel, Lock, UserRound } from 'lucide-react'
import { api, daysUntil, fmtDateTime, hoursUntil } from '../../../api'
import { useNameOf, usePersona } from '../../../shell/people'
import { Pill, Priority, Sla, StageClock } from '../../ui'
import type { Act, CaseDetail } from './types'

/**
 * Who the case is about and how urgent it is, in one strip: priority, the regulatory deadline, the stage clock,
 * the agent's view and the owner, with the ownership actions beside them. Then the workflow path.
 */
export default function CaseHeader({ d, act }: { d: CaseDetail; act: Act }) {
  const { case: c, party, access, agent_run: run } = d
  const nameOf = useNameOf()
  const persona = usePersona()
  const [restricting, setRestricting] = useState<null | 'restrict' | 'lift'>(null)
  const openStates = d.workflow.states.filter(s => s.kind === 'open' || (s.id === c.state && s.kind === 'wait'))
  const idx = openStates.findIndex(s => s.id === c.state)
  const related = <>
    {d.parent && <span>Reviewing <Link to={`/cases/${d.parent.id}`}>{d.parent.type_title} {d.parent.id}</Link></span>}
    {d.children.map(k => <span key={k.id}><Link to={`/cases/${k.id}`}>{k.type_title}</Link> <Pill tone={k.disposition === 'qa_failed' ? 'critical' : k.disposition === 'qa_passed' ? 'good' : 'neutral'}>{k.state_label}</Pill></span>)}
  </>
  return (
    <>
      <div className="crumbs"><Link to="/cases">Cases</Link> / {c.id}</div>
      <div className="case-h">
        <div>
          <h1>{party.name}</h1>
          <p>{c.type_title} <span className="muted">v{c.type_version}</span> · opened {fmtDateTime(c.created)} · <Link to={`/customers/${party.id}`}>Customer profile and network</Link></p>
        </div>
      </div>
      <div className="case-strip" role="group" aria-label="Case at a glance">
        <span className="cs-item"><Priority p={c.priority} /></span>
        <span className="cs-item"><Gavel size={15} aria-hidden /><span className="muted">{c.sla_clock || 'Deadline'}</span> <Sla days={daysUntil(c.sla_due)} open={c.open} /></span>
        {c.open && <span className="cs-item"><Clock size={15} aria-hidden /><span className="muted">Stage clock</span> <StageClock hours={hoursUntil(c.stage_due)} kind={c.state_kind} /></span>}
        {run && <span className="cs-item"><Bot size={15} aria-hidden /><Pill tone={run.recommendation === 'escalate' ? 'serious' : 'good'}>
          Agent: {run.recommendation === 'escalate' ? 'escalate' : 'close'} · {Math.round(run.confidence * 100)}%</Pill></span>}
        {c.open && <span className="cs-item"><UserRound size={15} aria-hidden /><span className="muted">{access.queue_label || 'No queue'}</span> · <b>{c.owner ? nameOf(c.owner) : 'Unassigned'}</b></span>}
        {access.read_only && <Pill tone="warning">Read-only access</Pill>}
        <span className="cs-actions">
          {c.open && access.can_claim && <button onClick={() => act(() => api.post(`/cases/${c.id}/claim`), 'Case claimed.')}>{c.owner ? 'Take over' : 'Claim'}</button>}
          {c.open && c.owner === persona?.id && <button onClick={() => act(() => api.post(`/cases/${c.id}/release`), 'Released back to the queue.')}>Release to queue</button>}
          {c.open && access.can_reassign && <select value="" aria-label="Reassign" onChange={e => e.target.value && act(() => api.post(`/cases/${c.id}/reassign`, { owner: e.target.value, reason: 'Reassigned by lead' }), 'Reassigned.')}>
            <option value="">Reassign to…</option>{access.queue_members.filter(m => m.id !== c.owner).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select>}
          {c.open && access.can_restrict && !c.restricted && <button className="ghost" title="Hide this case from everyone who is not cleared for restricted matters"
            onClick={() => setRestricting('restrict')}><Lock size={14} /> Restrict…</button>}
        </span>
      </div>
      {(d.parent || d.children.length > 0) && <div className="ownerbar">{related}</div>}
      {c.restricted && <div className="restricted" role="note">
        <b>Restricted case.</b> Only people cleared for restricted matters can see it. <span className="muted">{access.restricted_reason}</span>
        {access.can_restrict && <button onClick={() => setRestricting('lift')}>Lift restriction…</button>}
      </div>}
      {restricting && <form className="restrict-form" onSubmit={e => {
        e.preventDefault()
        const why = new FormData(e.currentTarget).get('why') as string, on = restricting === 'restrict'
        act(() => api.post(`/cases/${c.id}/restrict`, { restricted: on, reason: why }), on ? 'Case restricted. People who are not cleared can no longer see it.' : 'Restriction lifted.')
        setRestricting(null)
      }}>
        <label>{restricting === 'restrict' ? 'Why is this case restricted?' : 'Why is the restriction being lifted?'}
          <input name="why" required autoFocus placeholder={restricting === 'restrict' ? 'For example: the subject is a member of staff' : 'For example: the connection was disproved'} /></label>
        <button className="primary">{restricting === 'restrict' ? 'Restrict case' : 'Lift restriction'}</button><button type="button" onClick={() => setRestricting(null)}>Cancel</button>
        <span className="muted small">{restricting === 'restrict' ? 'The owner is released if they are not cleared. ' : ''}The reason is kept on the audit trail.</span>
      </form>}
      <ol className="stepper" aria-label="Workflow stage">
        {openStates.map((s, i) => <li key={s.id} className={!c.open || i < idx ? 'done' : i === idx ? 'now' : ''}>{s.label}</li>)}
        <li className={c.open ? '' : 'now end'}>{c.open ? 'Decision' : c.disposition === 'merged' ? 'Merged into another case' : c.state_label}</li>
      </ol>
    </>
  )
}
