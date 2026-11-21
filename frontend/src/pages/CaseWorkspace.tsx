import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, daysUntil, fmtDate, fmtDateTime, fmtMoney, hoursUntil, words } from '../api'
import { useApi } from '../useApi'
import FlowOfFunds from '../components/FlowOfFunds'
import EvidenceCard from '../components/kyc/EvidenceCard'
import type { Kyc } from '../components/kyc/types'
import { useNameOf, usePeople, usePersona } from '../App'
import { Card, Loading, Pill, Priority, Sla, StageClock } from '../components/ui'
import CaseExtras from '../components/CaseExtras'
import type { CaseRow } from './Cases'

type Finding = { text: string; cites: string[]; sources?: string[] }
type Answer = { question: string; answer: string; draft?: string; cites: string[]; sources: string[]; intent: string }
const ASKS = ['Why is this the recommendation?', 'Summarise this case', 'What are the largest transactions?', 'Draft a request to the customer', 'Draft a closure note']
type Detail = {
  case: CaseRow & { narrative: string; drafted_by: string | null; type_version: string }
  party: { id: string; name: string; kind: string; occupation: string; country: string; risk_rating: string; pep: boolean; onboarded: string; accounts: string[] }
  alerts: { id: string; title: string; version: string; typology: string; score: number; explanation: string; created: string; source: string; txn_ids: string[] }[]
  transactions: { id: string; ts: string; amount: number; direction: string; channel: string; counterparty: string; country: string; alerted: boolean }[]
  agent_run: null | { provider: string; recommendation: string; confidence: number; steps: { agent: string; title: string; summary: string; findings: Finding[]; outcome?: string; level?: string; rejected?: number }[] }
  events: { id: number; ts: string; actor: string; actor_kind: string; kind: string; payload: Record<string, unknown> }[]
  transitions: { to: string; label: string; to_label: string; needs_note: boolean; blocked: string | null; when: string; four_eyes: boolean; reasons: { code: string; label: string }[] }[]
  tasks: { id: string; label: string; required: boolean; done: boolean; done_by: string | null; done_at: string | null }[]
  requests: { id: string; recipient_label: string; question: string; status: string; raised_by: string; created: string; due: string; response: string; answered: string | null; answered_by: string | null }[]
  workflow: { states: { id: string; label: string; kind: string }[]; autonomy: string; recipients: Record<string, string> }
  access: { can_work: boolean; can_claim: boolean; can_reassign: boolean; read_only: boolean; can_restrict: boolean; restricted_reason: string; queue_members: { id: string; name: string }[]; queue_label: string }
  parent: CaseRow | null; children: CaseRow[]
  filing: null | { id: string; kind: string; filed_by: string; filed_at: string; sha256: string; status: string; reference: string; problems: string[] }
  other_cases: CaseRow[]
}

export default function CaseWorkspace() {
  const { id } = useParams()
  const persona = usePersona()
  const { data, error, reload } = useApi<Detail>(`/cases/${id}`, [persona?.id])
  const kyc = useApi<Kyc>(data ? `/customers/${data.party.id}/kyc` : null, [persona?.id])
  const [note, setNote] = useState('')
  const [reason, setReason] = useState('')
  const [restricting, setRestricting] = useState<null | 'restrict' | 'lift'>(null)
  const [narr, setNarr] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [focus, setFocus] = useState<string[]>([])
  const [onlyAlerted, setOnlyAlerted] = useState(true)
  const [rfi, setRfi] = useState({ recipient: 'customer', question: '', due_days: 5 })
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const [chat, setChat] = useState<Answer[]>([])
  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [tick, setTick] = useState(0) // bumps after every action so the side panels reload too
  const nameOf = useNameOf()
  const { can } = usePeople()
  useEffect(() => { if (data) setNarr(data.case.narrative) }, [data?.case.id, data?.case.narrative]) // eslint-disable-line
  if (!data) return <Loading error={error} />
  const { case: c, party, agent_run: run } = data
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); setMsg(ok); setNote(''); setReason(''); setTick(n => n + 1); reload() } catch (e) { setMsg((e as Error).message) }
  }
  const { access } = data
  const ask = (q: string) => {
    if (!q.trim()) return
    setAsking(true)
    api.post<Answer>(`/cases/${c.id}/copilot`, { question: q }).then(a => { setChat(x => [a, ...x]); setQuestion('') })
      .catch(e => setMsg(e.message)).finally(() => setAsking(false))
  }
  const openStates = data.workflow.states.filter(s => s.kind === 'open' || s.id === c.state && s.kind === 'wait')
  const idx = openStates.findIndex(s => s.id === c.state)
  const txns = data.transactions.filter(t => (focus.length ? focus.includes(t.id) : !onlyAlerted || t.alerted))
  const rec = run?.steps.find(s => s.agent === 'orchestrator')
  // Closing reasons differ by outcome, so the list offers every reason and each button accepts only its own.
  const reasons = [...new Map(data.transitions.flatMap(t => t.reasons).map(r => [r.code, r])).values()]
  const why = (t: Detail['transitions'][number]) => t.blocked ?? (t.needs_note && !note.trim() ? 'Add a rationale note first'
    : t.reasons.length && !t.reasons.some(r => r.code === reason) ? (reason ? 'The chosen reason does not apply to this outcome' : 'Choose a closing reason first') : '')
  return (
    <>
      <div className="crumbs"><Link to="/cases">Cases</Link> / {c.id}</div>
      <div className="case-h">
        <div>
          <h1>{party.name}</h1>
          <p>{c.type_title} <span className="muted">v{c.type_version}</span> · opened {fmtDateTime(c.created)} · <Link to={`/customers/${party.id}`}>Customer profile and network</Link></p>
        </div>
        <div className="case-meta">
          <Priority p={c.priority} />
          <span>{c.sla_clock}: <Sla days={daysUntil(c.sla_due)} open={c.open} /></span>
          {c.open && <span>Stage clock: <StageClock hours={hoursUntil(c.stage_due)} kind={c.state_kind} /></span>}
        </div>
      </div>
      {c.restricted && <div className="restricted" role="note">
        <b>Restricted case.</b> Only people cleared for restricted matters can see it. <span className="muted">{access.restricted_reason}</span>
        {access.can_restrict && <button onClick={() => setRestricting('lift')}>Lift restriction…</button>}
      </div>}
      {c.open && <div className="ownerbar">
        <span>Queue: <b>{access.queue_label || '–'}</b></span>
        <span>Owner: <b>{c.owner ? nameOf(c.owner) : 'Unassigned'}</b></span>
        {access.can_claim && <button onClick={() => act(() => api.post(`/cases/${c.id}/claim`), 'Case claimed.')}>{c.owner ? 'Take over' : 'Claim'}</button>}
        {c.owner === persona?.id && <button onClick={() => act(() => api.post(`/cases/${c.id}/release`), 'Released back to the queue.')}>Release to queue</button>}
        {access.can_reassign && <select value="" aria-label="Reassign" onChange={e => e.target.value && act(() => api.post(`/cases/${c.id}/reassign`, { owner: e.target.value, reason: 'Reassigned by lead' }), 'Reassigned.')}>
          <option value="">Reassign to…</option>{access.queue_members.filter(m => m.id !== c.owner).map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select>}
        {access.can_restrict && !c.restricted && <button className="ghost" title="Hide this case from everyone who is not cleared for restricted matters"
          onClick={() => setRestricting('restrict')}>Restrict…</button>}
        {access.read_only && <Pill tone="warning">Read-only access</Pill>}
        {data.parent && <span>Reviewing <Link to={`/cases/${data.parent.id}`}>{data.parent.type_title} {data.parent.id}</Link></span>}
        {data.children.map(k => <span key={k.id}><Link to={`/cases/${k.id}`}>{k.type_title}</Link> <Pill tone={k.disposition === 'qa_failed' ? 'critical' : k.disposition === 'qa_passed' ? 'good' : 'neutral'}>{k.state_label}</Pill></span>)}
      </div>}
      {!c.open && (data.parent || data.children.length > 0) && <div className="ownerbar">
        {data.parent && <span>Reviewing <Link to={`/cases/${data.parent.id}`}>{data.parent.type_title} {data.parent.id}</Link></span>}
        {data.children.map(k => <span key={k.id}><Link to={`/cases/${k.id}`}>{k.type_title}</Link> <Pill tone={k.disposition === 'qa_failed' ? 'critical' : k.disposition === 'qa_passed' ? 'good' : 'neutral'}>{k.state_label}</Pill></span>)}
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

      <div className="workspace">
        <div className="col">
          <Card title="Agent briefing" action={access.can_work ? <button className="ghost" onClick={() => act(() => api.post(`/cases/${c.id}/agents/run`), 'Agent investigation re-run.')}>Re-run</button> : undefined}>
            {!run ? <p className="empty">No agent run yet for this case.</p> : <>
              <div className={`rec ${run.recommendation}`}>
                <div><span className="muted small">Recommendation · autonomy {data.workflow.autonomy}</span>
                  <b>{run.recommendation === 'escalate' ? 'Escalate' : run.recommendation === 'close' ? 'Close, no further action' : 'No recommendation'}</b></div>
                <div className="conf"><span className="muted small">Confidence</span><b>{Math.round(run.confidence * 100)}%</b></div>
              </div>
              {rec && <ul className="signals">{rec.findings.map((f, i) => <li key={i} className={f.text.startsWith('+') ? 'up' : 'down'}>{f.text.slice(2)}</li>)}</ul>}
              <p className="muted small">The agent prepares and recommends. Only a person can move, close or file this case.</p>
              {run.steps.filter(s => s.agent !== 'orchestrator' && s.agent !== 'narrative_writer').map(s => s.outcome === 'skipped' || s.outcome === 'refused' ? (
                <p key={s.agent} className="skipped small"><b>{s.title}</b> {s.outcome === 'refused' ? 'was refused: ' : 'did not run: '}{s.summary}</p>) : (
                <details key={s.agent} open={['typology_analyst', 'sanctions_adjudicator', 'fraud_analyst', 'kyc_refresh'].includes(s.agent)}>
                  <summary><b>{s.title}</b><span className="muted"> {s.summary}</span></summary>
                  <ul className="findings">{s.findings.map((f, i) => (
                    <li key={i}>{f.text}{f.cites.length > 0 && <button className="cite" onClick={() => setFocus(f.cites)}>{f.cites.length} source{f.cites.length > 1 ? 's' : ''}</button>}
                      {f.sources && f.sources.length > 0 && <span className="srcs">{f.sources.slice(0, 3).join(' · ')}</span>}</li>))}</ul>
                  {!!s.rejected && <p className="muted small">{s.rejected} statement(s) cited nothing and were dropped.</p>}
                </details>))}
              <p className="muted small">Provider: {run.provider}. Every finding names the records it rests on.</p>
            </>}
          </Card>
          <Card title="Copilot" className="copilot">
            <p className="muted small">Ask about this case. It reads the case record, changes nothing, and drafts only for you to edit and send.</p>
            <div className="inline">{ASKS.map(q => <button key={q} className="ghost" disabled={asking} onClick={() => ask(q)}>{q}</button>)}</div>
            <form className="inline" onSubmit={e => { e.preventDefault(); ask(question) }}>
              <input aria-label="Question for the copilot" value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask about this case" style={{ flex: 1 }} maxLength={500} />
              <button disabled={asking || !question.trim()}>{asking ? 'Asking…' : 'Ask'}</button>
            </form>
            {chat.map((a, i) => (
              <div className="answer" key={i}>
                <div className="q">{a.question}</div>
                <div>{a.answer}</div>
                {a.draft && <><pre>{a.draft}</pre>
                  {a.intent === 'draft_request' && access.can_work && <button className="ghost" onClick={() => setRfi(r => ({ ...r, question: a.draft! }))}>Use as an information request</button>}
                  {a.intent === 'draft_note' && <button className="ghost" onClick={() => setNote(a.draft!)}>Use as my note</button>}</>}
                <div className="small">{a.cites.length > 0 && <button className="cite" onClick={() => setFocus(a.cites)}>{a.cites.length} transaction{a.cites.length > 1 ? 's' : ''}</button>}
                  <span className="srcs">{a.sources.slice(0, 4).join(' · ')}</span></div>
              </div>))}
          </Card>
          <Card title={`Alerts (${data.alerts.length})`}>
            {data.alerts.length === 0 && <p className="empty">No alerts. This case was opened by a schedule, an intake channel or another case.</p>}
            {data.alerts.map(a => (
              <div className="alert" key={a.id}>
                <div><b>{a.title}</b> <Pill>{a.typology}</Pill><span className="score">score {a.score.toFixed(2)}</span></div>
                <p>{a.explanation}</p>
                <div className="muted small">{a.id} · {a.version === 'external' ? 'raised by an outside engine' : `scenario v${a.version}`} · {a.source} · <button className="cite" onClick={() => setFocus(a.txn_ids)}>show {a.txn_ids.length} transactions</button></div>
              </div>))}
          </Card>
        </div>

        <div className="col">
          <Card title="Customer">
            <dl className="facts">
              <dt>Customer ID</dt><dd>{party.id}</dd><dt>Occupation</dt><dd>{party.occupation || '–'}</dd>
              <dt>Risk rating</dt><dd><Pill tone={party.risk_rating === 'high' ? 'serious' : party.risk_rating === 'medium' ? 'warning' : 'neutral'}>{party.risk_rating}</Pill>{party.pep && <Pill tone="serious">PEP</Pill>}</dd>
              <dt>Customer since</dt><dd>{party.onboarded}</dd><dt>Accounts</dt><dd>{party.accounts.join(', ')}</dd>
              <dt>Other cases</dt><dd>{data.other_cases.length === 0 ? 'None' : data.other_cases.map(o => <Link key={o.id} to={`/cases/${o.id}`}>{o.id} </Link>)}</dd>
            </dl>
          </Card>
          <Card title="Transactions" action={focus.length
            ? <button className="ghost" onClick={() => setFocus([])}>Clear source filter ({focus.length})</button>
            : <label className="check"><input type="checkbox" checked={onlyAlerted} onChange={e => setOnlyAlerted(e.target.checked)} /> Alerted only</label>}>
            {txns.length === 0 ? <p className="empty">No transactions to show.</p> :
              <div className="scroll tall"><table>
                <thead><tr><th>When</th><th>Channel</th><th>Counterparty</th><th className="num">In</th><th className="num">Out</th></tr></thead>
                <tbody>{txns.map(t => (
                  <tr key={t.id} className={t.alerted ? 'hit' : ''}>
                    <td className="nowrap">{fmtDateTime(t.ts)}</td><td>{t.channel}</td>
                    <td>{t.counterparty || <span className="muted">Cash</span>}{t.country !== 'US' && <Pill>{t.country}</Pill>}</td>
                    <td className="num">{t.direction === 'in' ? fmtMoney(t.amount) : ''}</td><td className="num">{t.direction === 'out' ? fmtMoney(t.amount) : ''}</td>
                  </tr>))}</tbody>
              </table></div>}
          </Card>
        </div>

        <div className="col">
          <Card title="Decision">
            {data.filing && <div className="filed"><b>{data.filing.kind} {data.filing.status === 'acknowledged' ? `accepted by the regulator (${data.filing.reference})` : data.filing.status === 'submitted' ? 'sent to the regulator' : data.filing.status === 'rejected' ? 'rejected by the regulator' : data.filing.problems.length ? 'prepared but not sent: it needs fixing' : 'prepared, not sent yet'}</b> · prepared by {nameOf(data.filing.filed_by)} on {fmtDateTime(data.filing.filed_at)}<code>{data.filing.id} · sha256 {data.filing.sha256.slice(0, 16)}…</code>{(can('submit_filings') || can('view_team')) && <Link to={`/filings/${data.filing.id}`}>Open the report</Link>}</div>}
            {!c.open && !data.filing && c.disposition !== 'merged' && <p className="empty">Closed: {c.state_label}.</p>}
            {c.open && <>
              {data.tasks.length > 0 && <fieldset className="tasks"><legend>Tasks in this stage</legend>
                {data.tasks.map(t => (
                  <label key={t.id} className={t.done ? 'done' : ''}>
                    <input type="checkbox" checked={t.done} disabled={!access.can_work}
                      onChange={e => act(() => api.post(`/cases/${c.id}/tasks/${t.id}`, { done: e.target.checked }), e.target.checked ? 'Task done.' : 'Task reopened.')} />
                    <span>{t.label}{!t.required && <Pill>optional</Pill>}
                      {t.done && <small>{nameOf(t.done_by)}, {fmtDateTime(t.done_at!)}</small>}</span>
                  </label>))}
              </fieldset>}
              <label className="lbl" htmlFor="note">Rationale note</label>
              <textarea id="note" rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="Required for closures and returns" />
              {reasons.length > 0 && <>
                <label className="lbl" htmlFor="reason">Closing reason</label>
                <select id="reason" value={reason} onChange={e => setReason(e.target.value)} style={{ width: '100%' }}>
                  <option value="">Choose a reason (needed to close)</option>{reasons.map(r => <option key={r.code} value={r.code}>{r.label}</option>)}
                </select></>}
              <div className="actions">{data.transitions.map(t => (
                <button key={t.to + t.label} disabled={!!why(t)} title={why(t)}
                  className={/file|block|refund$/i.test(t.label) ? 'primary' : ''}
                  onClick={() => act(() => api.post(`/cases/${c.id}/transition`, { to: t.to, note, reason: t.reasons.length ? reason : '' }), `Moved to ${t.to_label}.`)}>
                  {t.label}{t.four_eyes && !t.blocked && <em className="tag">four-eyes</em>}{t.blocked && <small>{t.blocked}</small>}
                  {t.when && <small className="why">Offered because {t.when}</small>}
                </button>))}
                {data.transitions.length === 0 && <p className="empty">No actions from this stage.</p>}
              </div>
              <p className="muted small">Acting as {persona?.name} ({persona?.title}). Switch person at the top to see what each role can do here.</p>
            </>}
            {msg && <p className="msg" role="status">{msg}</p>}
          </Card>
          <CaseExtras caseId={c.id} tick={tick} onChange={(m: string) => { setMsg(m); setTick(n => n + 1); reload() }} />
          <Card title="Narrative" action={<span className="muted small">{c.drafted_by ? `Saved by ${nameOf(c.drafted_by)}` : narr ? 'Agent draft, not yet reviewed' : ''}</span>}>
            <textarea rows={11} value={narr} onChange={e => setNarr(e.target.value)} disabled={!c.open || !access.can_work} aria-label="Narrative" />
            {c.open && access.can_work && <button onClick={() => act(() => api.post(`/cases/${c.id}/narrative`, { text: narr }), 'Narrative saved under your name.')} disabled={!narr.trim()}>Save as my narrative</button>}
          </Card>
          <Card title={`Information requests (${data.requests.length})`}>
            {data.requests.length === 0 && <p className="empty">None raised.</p>}
            {data.requests.map(r => (
              <div className="rfi" key={r.id}>
                <div><b>{r.recipient_label}</b> <Pill tone={r.status === 'answered' ? 'good' : new Date(r.due) < new Date() ? 'critical' : 'warning'}>
                  {r.status === 'answered' ? 'answered' : new Date(r.due) < new Date() ? 'overdue' : `due ${fmtDate(r.due)}`}</Pill>
                  <span className="muted small"> asked by {nameOf(r.raised_by)}, {fmtDate(r.created)}</span></div>
                <p>{r.question}</p>
                {r.status === 'answered' ? <p className="answer">{r.response}<span className="muted small"> recorded by {nameOf(r.answered_by)}, {fmtDate(r.answered!)}</span></p>
                  : c.open && access.can_work && <div className="inline">
                    <input type="search" placeholder="Record the response" value={answers[r.id] ?? ''} onChange={e => setAnswers({ ...answers, [r.id]: e.target.value })} aria-label="Response" />
                    <button disabled={!(answers[r.id] ?? '').trim()} onClick={() => act(() => api.post(`/requests/${r.id}/answer`, { response: answers[r.id] }), 'Response recorded.')}>Record</button></div>}
              </div>))}
            {c.open && access.can_work && <details className="newrfi"><summary>Raise a request</summary>
              <div className="inline">
                <select value={rfi.recipient} onChange={e => setRfi({ ...rfi, recipient: e.target.value })} aria-label="Recipient">
                  {Object.entries(data.workflow.recipients).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
                <select value={rfi.due_days} onChange={e => setRfi({ ...rfi, due_days: Number(e.target.value) })} aria-label="Due">
                  {[2, 5, 10, 20].map(d => <option key={d} value={d}>Due in {d} days</option>)}</select>
              </div>
              <textarea rows={2} placeholder="What do you need to know?" value={rfi.question} onChange={e => setRfi({ ...rfi, question: e.target.value })} aria-label="Question" />
              <button disabled={!rfi.question.trim()} onClick={() => act(() => api.post(`/cases/${c.id}/requests`, rfi).then(() => setRfi({ ...rfi, question: '' })), 'Request raised.')}>Send request</button>
            </details>}
          </Card>
          <Card title="Audit trail">
            <ul className="events tight">{data.events.map(e => (
              <li key={e.id}><Pill tone={e.actor_kind === 'agent' ? 'agent' : e.actor_kind === 'human' ? 'human' : 'neutral'}>{e.actor_kind}</Pill>
                <span><b>{words(e.kind.replace('.', ' '))}</b> · {nameOf(e.actor)}{typeof e.payload.note === 'string' && e.payload.note ? <em> “{e.payload.note}”</em> : null}</span>
                <time>{fmtDateTime(e.ts)}</time></li>))}</ul>
          </Card>
        </div>
      </div>
      <FlowOfFunds partyId={party.id} caseId={c.id} />
      {kyc.data && <EvidenceCard partyId={party.id} caseId={c.id} view={kyc.data.evidence} onChange={kyc.reload} />}
    </>
  )
}
