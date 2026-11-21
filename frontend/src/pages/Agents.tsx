import { Fragment, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDateTime, getPersonaId, getTenantId, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf } from '../shell/people'
import { Card, Kpi, Loading, Pill } from '../components/ui'

type Level = 'L0' | 'L1' | 'L2' | 'L3'
type Usage = { done: number; skipped: number; refused: number; acted: number; rejected_claims: number; last: string | null }
type Agent = {
  id: string; title: string; version: string; kind: string; goal: string; tools: string[]; reads_outside_content: boolean; max_level: Level
  acts: string | null; acts_label: string | null; enabled: boolean; levels: Record<string, Level>; highest_level_here: Level
  evaluation_set: string; evaluation: null | { passed: boolean; why: string; items: number; at: string }; usage: Usage; model: string
}
type EvalSet = { passed: boolean; why: string; items: number; agreement?: number; missed?: number; over_escalated?: number; would_close?: number
  wrongly_closed?: number; disagreements?: { case_id?: string; alert_id?: string; people: string; agents: string }[] }
type Evaluation = { job_id: string; at: string; by: string; passed: boolean; fingerprint: string; sets: Record<string, EvalSet> }
type Body = { agents: Record<string, { enabled: boolean; levels: Record<string, Level> }>; limits: Record<string, number>
  decision: { escalate_at: number }; challenger: null | { name: string; decision: { escalate_at: number } } }
type Closed = { id: string; scenario: string; party_name: string; score: number; created: string; status: string; closed_by: string; why: string
  qa_sample: boolean; qa_by: string | null; qa_result: string | null; case_id: string | null }
type Person = { person: string; name: string; title: string; decisions: number; agreed: number; agreement: number; overrode_to_escalate: number
  overrode_to_close: number; review: boolean }
type Side = { items: number; agreement: number; missed: number; over_escalated: number; escalate_at: number }
type Job = { id: string; kind: string; params: Record<string, unknown>; started_by: string; started: string; result: any }
type Page = {
  client: string; policy_version: number; fingerprint: string; limits: Record<string, number>; decision: { escalate_at: number }
  agents: Agent[]; case_types: { id: string; title: string }[]; levels: { id: Level; label: string }[]; level_help: Record<Level, string>
  human_only: string[]; controls: string[]; ledger: { intact: boolean; rows: number }; can_manage: boolean; can_review: boolean; manager: string
  policy: Body; history: { version: number; published_by: string; published: string; note: string }[]; evaluation: Evaluation | null
  last_evaluation: null | { at: string; by: string; passed: boolean; covers_current_settings: boolean }
  challenger_report: null | { name: string; champion: Side; challenger: Side; live_runs: number; live_differences: number; better: boolean }
  agreement: { people: Person[]; decisions: number; agreement: number | null; rule: string }
  closed_by_agents: Closed[]; awaiting_review: number; jobs: Job[]
}
type Check = { ok: boolean; error?: string; changes?: string[]; blockers?: string[]; publishable?: boolean }

const ORDER: Level[] = ['L0', 'L1', 'L2', 'L3']
const SETS: Record<string, string> = { case_dispositions: 'Briefings against closed cases', alert_outcomes: 'Triage against past alerts',
  screening_outcomes: 'Screening discounts against past hits', copilot_questions: 'Copilot answers cite the record' }
const LIMITS: Record<string, [string, string]> = {
  close_alert_below: ['Triage may close an alert scoring under', 'Only when nothing else about the customer is a concern'],
  discount_hit_below: ['Adjudicator may discount a hit with name similarity under', 'Never when a high-risk country is involved'],
  qa_sample: ['Share of agent-closed alerts a person must check', 'Chosen by the alert id, so nobody can pick'],
}
const pct = (x: number | null | undefined) => (x === null || x === undefined ? '–' : `${Math.round(x * 100)}%`)
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x))

function Result({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <Pill tone={ok ? 'good' : 'critical'}>{children}</Pill>
}

export default function Agents() {
  const nameOf = useNameOf()
  const { data, error, reload } = useApi<Page>('/agents')
  const [draft, setDraft] = useState<Body | null>(null)
  const [check, setCheck] = useState<Check | null>(null)
  const [draftEval, setDraftEval] = useState<Evaluation | null>(null)
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [reason, setReason] = useState<Record<string, string>>({})
  const [open, setOpen] = useState<string | null>(null)
  const [days, setDays] = useState(90)
  const [version, setVersion] = useState<Page['policy_version'] | null>(null)
  if (data && version !== data.policy_version) { setVersion(data.policy_version); setDraft(clone(data.policy)); setCheck(null); setDraftEval(null) }  // a new version: edit from it
  const dirty = useMemo(() => !!data && !!draft && JSON.stringify(draft) !== JSON.stringify(data.policy), [data, draft])
  if (!data || !draft) return <Loading error={error} />
  const can = data.can_manage

  const act = (key: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(key); setMsg(null)
    return fn().then(() => { if (done) setMsg(done); reload() }).catch(e => setMsg(e.message)).finally(() => setBusy(''))
  }
  const edit = (fn: (d: Body) => void) => { const d = clone(draft); fn(d); setDraft(d); setCheck(null); setDraftEval(null) }
  const levelOf = (a: Agent, ct: string): Level => draft.agents[a.id]?.levels?.[ct] ?? data.policy.agents[a.id]?.levels?.[ct] ?? a.levels[ct]
  const setLevel = (a: Agent, ct: string, level: Level) => edit(d => {
    const entry = d.agents[a.id] ?? (d.agents[a.id] = { enabled: true, levels: {} })
    entry.levels = { ...entry.levels, [ct]: level }
  })
  const runCheck = () => act('check', () => api.post<Check>('/agents/policy/check', { policy: draft }).then(setCheck))
  const evaluateDraft = () => act('eval', () => api.post<Job>('/agents/evaluations', { policy: draft })
    .then(j => { setDraftEval({ ...j.result, job_id: j.id, at: j.started, by: j.started_by }); return api.post<Check>('/agents/policy/check', { policy: draft }).then(setCheck) }))
  const publish = () => act('publish', () => api.post('/agents/policy/publish', { policy: draft }), 'Agent policy published.')
  const flip = (a: Agent) => act(`switch-${a.id}`, () => api.post(`/agents/${a.id}/switch`, { on: !a.enabled, reason: reason[a.id] ?? '' }),
    `${a.title} switched ${a.enabled ? 'off' : 'on'}.`).then(() => setReason(r => ({ ...r, [a.id]: '' })))
  const download = () => fetch('/api/agents/inventory.md', { headers: { 'X-Tenant': getTenantId(), 'X-Persona': getPersonaId() } })
    .then(r => r.blob()).then(b => { const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = `agent-inventory-${data.client}.md`; a.click() })

  const acting = data.agents.flatMap(a => Object.entries(a.levels).filter(([, l]) => l === 'L3').map(([ct]) => `${a.title}: ${data.case_types.find(c => c.id === ct)?.title ?? ct}`))
  const shown = draftEval ?? data.evaluation
  const lookback = data.jobs.find(j => j.kind === 'lookback')
  const tuning = data.jobs.find(j => j.kind === 'tuning')
  const columns = data.case_types.filter(ct => data.agents.some(a => a.levels[ct.id] !== 'L0' || data.policy.agents[a.id]?.levels?.[ct.id]))

  return (
    <>
      <div className="page-h case-h"><div><h1>Agents</h1>
        <p>Which agents work for {data.client === getTenantId() ? 'this client' : data.client}, how far each may go, and the record of everything they have done.</p></div>
        <div className="inline">
          <button disabled={!can || !!busy} onClick={() => act('eval-now', () => api.post('/agents/evaluations', {}), 'Evaluation finished.')}>{busy === 'eval-now' ? 'Evaluating…' : 'Evaluate current settings'}</button>
          <button onClick={download}>Download documentation pack</button>
        </div></div>
      {!can && <p className="muted small">You can see the agents and their record. Changing anything needs the manage agents permission, held by {data.manager}.</p>}
      {msg && <p className="msg" role="status">{msg}</p>}
      <div className="kpis">
        <Kpi label="Agents switched on" value={`${data.agents.filter(a => a.enabled).length} of ${data.agents.length}`} sub={`Policy version ${data.policy_version}`} />
        <Kpi label="Acting on their own" value={acting.length} sub={acting.length ? acting.join('; ') : 'Every item is decided by a person'} />
        <Kpi label="Evaluation of current settings" value={data.evaluation ? (data.evaluation.passed ? 'Passed' : 'Not passed') : 'Not run'}
             sub={data.evaluation ? `${fmtDateTime(data.evaluation.at + 'Z')} by ${nameOf(data.evaluation.by)}` : 'Run it before raising autonomy'} bad={!!data.evaluation && !data.evaluation.passed} />
        <Kpi label="Agent ledger" value={data.ledger.rows.toLocaleString()} sub={data.ledger.intact ? 'Entries; chain intact' : 'Chain broken: investigate'} bad={!data.ledger.intact} />
        <Kpi label="Agent closures to check" value={data.awaiting_review} sub="Sampled for a person" bad={data.awaiting_review > 0} />
      </div>

      <Card title="Autonomy by agent and case type" action={<span className="muted small">{data.levels.map(l => `${l.id} ${l.label}`).join(' · ')}</span>}>
        <div className="scroll"><table className="matrix dial">
          <thead><tr><th>Agent</th>{columns.map(ct => <th key={ct.id}>{ct.title}</th>)}<th>Switch</th></tr></thead>
          <tbody>{data.agents.map(a => (
            <tr key={a.id} className={a.enabled ? '' : 'off'}>
              <td><button className="ghost link" onClick={() => setOpen(open === a.id ? null : a.id)} aria-expanded={open === a.id}><b>{a.title}</b></button>
                <div className="muted small">{words(a.kind)} · up to {a.max_level}{a.acts_label ? ` · at L3: ${a.acts_label.toLowerCase()}` : ''}</div></td>
              {columns.map(ct => {
                const level = a.enabled ? levelOf(a, ct.id) : 'L0'
                const changed = a.enabled && level !== a.levels[ct.id]
                return <td key={ct.id} className={`lv ${level}${changed ? ' changed' : ''}`}>
                  <select aria-label={`${a.title} on ${ct.title}`} value={level} disabled={!can || !a.enabled} title={data.level_help[level]}
                          onChange={e => setLevel(a, ct.id, e.target.value as Level)}>
                    {ORDER.filter(l => ORDER.indexOf(l) <= ORDER.indexOf(a.max_level)).map(l => <option key={l} value={l}>{l}</option>)}
                  </select></td>
              })}
              <td className="nowrap">
                {can ? <span className="inline tight">
                  <input aria-label={`Reason for switching ${a.title}`} placeholder="Reason" value={reason[a.id] ?? ''} onChange={e => setReason(r => ({ ...r, [a.id]: e.target.value }))} />
                  <button className={a.enabled ? 'danger' : ''} disabled={!!busy || !(reason[a.id] ?? '').trim()} onClick={() => flip(a)}>{a.enabled ? 'Switch off' : 'Switch on'}</button>
                </span> : <Pill tone={a.enabled ? 'good' : 'critical'}>{a.enabled ? 'On' : 'Off'}</Pill>}
              </td>
            </tr>))}</tbody>
        </table></div>
        {open && (() => { const a = data.agents.find(x => x.id === open)!; return (
          <div className="hit pad">
            <p><b>{a.title}</b> {a.version}. {a.goal}</p>
            <div className="facts">
              <span>Tools</span><span>{a.tools.map(words).join(', ')}</span>
              <span>Model</span><span>{a.model}</span>
              <span>Outside content</span><span>{a.reads_outside_content ? 'Reads news and registries, only through the extractor' : 'None'}</span>
              <span>Work so far</span><span>{a.usage.done} step(s), {a.usage.acted} action(s) on its own, {a.usage.refused} refused, {a.usage.rejected_claims} uncited claim(s) dropped</span>
              <span>Evaluation</span><span>{a.evaluation ? <><Result ok={a.evaluation.passed}>{a.evaluation.passed ? 'Passed' : 'Not passed'}</Result> {a.evaluation.why}</> : 'Not run for the current settings'}</span>
            </div>
          </div>) })()}
        <div className="cols-2 gap-top">
          <div>
            <h3>Limits agents act inside</h3>
            {Object.entries(LIMITS).map(([k, [label, help]]) => (
              <label className="limit" key={k}><span>{label}<small className="muted"> {help}</small></span>
                <input type="number" step="0.01" min="0" max="1" disabled={!can} value={draft.limits[k]} onChange={e => edit(d => { d.limits[k] = Number(e.target.value) })} /></label>))}
            <label className="limit"><span>Weight of evidence at which a briefing recommends escalation<small className="muted"> Lower is more cautious</small></span>
              <input type="number" step="0.01" min="0.05" max="0.6" disabled={!can} value={draft.decision.escalate_at} onChange={e => edit(d => { d.decision.escalate_at = Number(e.target.value) })} /></label>
          </div>
          <div>
            <h3>Challenger, in shadow</h3>
            <p className="muted small">An alternative threshold runs silently beside the one in force. Its recommendation is recorded for comparison and never shown on a case.</p>
            <div className="inline">
              <input aria-label="Challenger name" placeholder="Name, for example stricter" disabled={!can} value={draft.challenger?.name ?? ''}
                     onChange={e => edit(d => { d.challenger = e.target.value ? { name: e.target.value, decision: d.challenger?.decision ?? { escalate_at: 0.35 } } : null })} />
              <input aria-label="Challenger threshold" type="number" step="0.01" min="0.05" max="0.6" disabled={!can || !draft.challenger} value={draft.challenger?.decision.escalate_at ?? ''}
                     onChange={e => edit(d => { if (d.challenger) d.challenger.decision.escalate_at = Number(e.target.value) })} />
            </div>
            {data.challenger_report && <table><thead><tr><th></th><th className="num">Threshold</th><th className="num">Agrees with people</th><th className="num">Missed</th><th className="num">Over-escalated</th></tr></thead>
              <tbody>
                <tr><td>In force</td><td className="num">{data.challenger_report.champion.escalate_at}</td><td className="num">{pct(data.challenger_report.champion.agreement)}</td><td className="num">{data.challenger_report.champion.missed}</td><td className="num">{data.challenger_report.champion.over_escalated}</td></tr>
                <tr><td>{data.challenger_report.name}</td><td className="num">{data.challenger_report.challenger.escalate_at}</td><td className="num">{pct(data.challenger_report.challenger.agreement)}</td><td className="num">{data.challenger_report.challenger.missed}</td><td className="num">{data.challenger_report.challenger.over_escalated}</td></tr>
              </tbody></table>}
            {data.challenger_report && <p className="muted small">On {data.challenger_report.champion.items} closed cases. Live: {data.challenger_report.live_runs} briefing(s) in shadow, {data.challenger_report.live_differences} where the two differed. {data.challenger_report.better ? 'The challenger did better on history.' : 'The challenger did not do better on history.'}</p>}
          </div>
        </div>
        {can && dirty && <div className="draftbar">
          <b>Unpublished changes.</b>
          <button disabled={!!busy} onClick={runCheck}>Check</button>
          <button disabled={!!busy} onClick={evaluateDraft}>{busy === 'eval' ? 'Evaluating…' : 'Evaluate this draft'}</button>
          <button className="primary" disabled={!!busy || !check?.publishable} onClick={publish}>Publish</button>
          <button className="ghost" disabled={!!busy} onClick={() => { setDraft(clone(data.policy)); setCheck(null); setDraftEval(null) }}>Discard</button>
        </div>}
        {check && (check.ok ? <div className="checkout">
          {check.changes!.length === 0 ? <p className="muted">Nothing would change.</p> : <ul>{check.changes!.map(c => <li key={c}>{c}</li>)}</ul>}
          {check.blockers!.map(b => <p className="msg" key={b}>{b}</p>)}
          {check.publishable && <p className="ok">Ready to publish.</p>}
        </div> : <p className="msg">{check.error}</p>)}
      </Card>

      <div className="cols-2">
        <Card title={draftEval ? 'Evaluation of this draft' : 'Evaluation of the settings in force'}>
          {!shown ? <p className="empty">Not run yet. An evaluation replays this client's own closed cases and alerts and compares the agents with what people decided.</p> : <>
            <table><thead><tr><th>Measured</th><th>Result</th><th className="num">Items</th><th>Finding</th></tr></thead>
              <tbody>{Object.entries(shown.sets).map(([k, v]) => (
                <tr key={k}><td>{SETS[k] ?? words(k)}</td><td><Result ok={v.passed}>{v.passed ? 'Passed' : 'Not passed'}</Result></td><td className="num">{v.items}</td>
                  <td>{v.why}{v.disagreements && v.disagreements.length > 0 && <details><summary className="small">{v.disagreements.length} disagreement(s)</summary>
                    <ul className="plain small">{v.disagreements.map((d, i) => <li key={i}>{d.case_id ? <Link to={`/cases/${d.case_id}`}>{d.case_id}</Link> : d.alert_id}: people {words(d.people)}, agents {d.agents}</li>)}</ul></details>}</td></tr>))}</tbody></table>
            <p className="muted small">Run {fmtDateTime(shown.at + 'Z')} by {nameOf(shown.by)}. An agent may act on its own only under settings whose evaluation passed.</p>
          </>}
        </Card>
        <Card title="People and the recommendation">
          {data.agreement.people.length === 0 ? <p className="empty">No closed cases with a recommendation yet.</p> : <>
            <table><thead><tr><th>Person</th><th className="num">Decisions</th><th className="num">Agreed</th><th className="num">Chose to escalate</th><th className="num">Chose to close</th><th></th></tr></thead>
              <tbody>{data.agreement.people.map(p => (
                <tr key={p.person}><td>{p.name}<div className="muted small">{p.title}</div></td><td className="num">{p.decisions}</td><td className="num">{pct(p.agreement)}</td>
                  <td className="num">{p.overrode_to_escalate}</td><td className="num">{p.overrode_to_close}</td><td>{p.review && <Pill tone="warning">Review</Pill>}</td></tr>))}</tbody></table>
            <p className="muted small">Overall {pct(data.agreement.agreement)} agreement across {data.agreement.decisions} decisions. {data.agreement.rule} Agreement that high can mean the briefings are good or that they are not being questioned.</p>
          </>}
        </Card>
      </div>

      <Card title="Alerts closed by agents">
        {data.closed_by_agents.length === 0 ? <p className="empty">None. No agent is acting on its own, or nothing has fallen inside its limits.</p> :
          <table><thead><tr><th>Alert</th><th>Customer</th><th className="num">Score</th><th>Closed by</th><th>Why</th><th>Check</th></tr></thead>
            <tbody>{data.closed_by_agents.map(a => (
              <tr key={a.id}><td>{words(a.scenario)}<div className="muted small">{a.id} · {fmtDateTime(a.created + 'Z')}</div></td><td>{a.party_name}</td><td className="num">{a.score.toFixed(2)}</td>
                <td>{words(a.closed_by.replace('agent:', ''))}</td><td>{a.why}</td>
                <td className="nowrap">{a.qa_result ? <>{a.qa_result === 'confirmed' ? <Pill tone="good">Agreed</Pill> : <Pill tone="warning">Reopened</Pill>} <span className="muted small">{nameOf(a.qa_by)}</span>{a.case_id && <> · <Link to={`/cases/${a.case_id}`}>case</Link></>}</> :
                  <span className="inline tight">{a.qa_sample && <Pill tone="warning">Sampled</Pill>}
                    <button disabled={!data.can_review || !!busy} onClick={() => act(`qa-${a.id}`, () => api.post(`/agents/closed-alerts/${a.id}/review`, { confirm: true }), 'Closure agreed.')}>Agree</button>
                    <button disabled={!data.can_review || !!busy} onClick={() => act(`qa-${a.id}`, () => api.post(`/agents/closed-alerts/${a.id}/review`, { confirm: false }), 'Alert reopened into a case.')}>Reopen</button></span>}</td></tr>))}</tbody></table>}
      </Card>

      <div className="cols-2">
        <Card title="Lookback" action={<span className="inline tight"><input aria-label="Days to look back" type="number" min="1" max="3650" value={days} onChange={e => setDays(Number(e.target.value))} style={{ width: 80 }} />
          <button disabled={!can || !!busy} onClick={() => act('lookback', () => api.post('/agents/lookback', { days }), 'Lookback finished.')}>{busy === 'lookback' ? 'Running…' : 'Run'}</button></span>}>
          {!lookback ? <p className="empty">Re-researches closed cases with today's agents and lists the ones worth a second look. It changes none of them.</p> : <>
            <p>Re-researched <b>{lookback.result.reviewed}</b> case(s) closed in the last {String(lookback.params.days)} days. <b>{lookback.result.flagged}</b> worth a second look, of which <b>{lookback.result.missed_then}</b> were closed without action.</p>
            {lookback.result.items.length > 0 && <table><thead><tr><th>Case</th><th>Customer</th><th>Then</th><th>Agents now</th><th>Why</th></tr></thead>
              <tbody>{lookback.result.items.slice(0, 12).map((i: any) => (
                <tr key={i.case_id}><td><Link to={`/cases/${i.case_id}`}>{i.case_id}</Link></td><td>{i.party_name}</td><td>{words(i.outcome_then)}</td>
                  <td><Pill tone={i.agents_now === 'escalate' ? 'serious' : 'good'}>{i.agents_now}</Pill></td><td className="small">{i.reasons.join('; ') || i.concern}</td></tr>))}</tbody></table>}
            <p className="muted small">Run {fmtDateTime(lookback.started + 'Z')} by {nameOf(lookback.started_by)}.</p>
          </>}
        </Card>
        <Card title="Scenario tuning" action={<button disabled={!can || !!busy} onClick={() => act('tuning', () => api.post('/agents/tuning'), 'Tuning review finished.')}>{busy === 'tuning' ? 'Running…' : 'Run'}</button>}>
          {!tuning ? <p className="empty">Shows how much of what each scenario raised mattered, and what a higher score cut-off would have cost. It proposes; it changes nothing.</p> : <>
            <table><thead><tr><th>Scenario</th><th className="num">Alerts</th><th className="num">Led somewhere</th><th>Proposal</th></tr></thead>
              <tbody>{tuning.result.scenarios.map((x: any) => (
                <Fragment key={x.scenario}><tr><td>{x.title} {x.noisy && <Pill tone="warning">Noisy</Pill>}</td><td className="num">{x.alerts}</td><td className="num">{pct(x.productive_rate)}</td>
                  <td className="small">{x.proposal ? x.proposal.text : x.note}</td></tr></Fragment>))}</tbody></table>
            <p className="muted small">{tuning.result.caution} Run {fmtDateTime(tuning.started + 'Z')} by {nameOf(tuning.started_by)}.</p>
          </>}
        </Card>
      </div>

      <div className="cols-2">
        <Card title="Reserved for people">
          <ul className="plain">{data.human_only.map(x => <li key={x}>{x}</li>)}</ul>
          <p className="muted small">These are not tools. No agent, at any autonomy level, at any client, can do them.</p>
        </Card>
        <Card title="Controls built into the runtime">
          <ul className="plain">{data.controls.map(x => <li key={x}>{x}</li>)}</ul>
        </Card>
      </div>

      <Card title="Policy history">
        {data.history.length === 0 ? <p className="empty">The starting policy is in force: every agent prepares and recommends, and none acts on its own.</p> :
          <table><thead><tr><th>Version</th><th>Published</th><th>By</th><th>What changed</th></tr></thead>
            <tbody>{data.history.map(v => <tr key={v.version}><td>{v.version}</td><td className="nowrap">{fmtDateTime(v.published + 'Z')}</td><td>{nameOf(v.published_by)}</td><td>{v.note}</td></tr>)}</tbody></table>}
      </Card>
    </>
  )
}
