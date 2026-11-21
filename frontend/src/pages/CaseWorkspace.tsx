import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, daysUntil, fmtDateTime, fmtMoney } from '../api'
import { useApi } from '../useApi'
import { usePersona } from '../App'
import { Card, Loading, Pill, Priority, Sla } from '../components/ui'
import type { CaseRow } from './Cases'

type Finding = { text: string; cites: string[] }
type Detail = {
  case: CaseRow & { narrative: string; drafted_by: string | null; type_version: string }
  party: { id: string; name: string; kind: string; occupation: string; country: string; risk_rating: string; pep: boolean; onboarded: string; accounts: string[] }
  alerts: { id: string; title: string; version: string; typology: string; score: number; explanation: string; created: string; source: string; txn_ids: string[] }[]
  transactions: { id: string; ts: string; amount: number; direction: string; channel: string; counterparty: string; country: string; alerted: boolean }[]
  agent_run: null | { provider: string; recommendation: string; confidence: number; steps: { agent: string; title: string; summary: string; findings: Finding[] }[] }
  events: { id: number; ts: string; actor: string; actor_kind: string; kind: string; payload: Record<string, unknown> }[]
  transitions: { to: string; label: string; to_label: string; needs_note: boolean; blocked: string | null }[]
  workflow: { states: { id: string; label: string; kind: string }[]; autonomy: string }
  filing: null | { id: string; filed_by: string; filed_at: string; sha256: string }
  other_cases: CaseRow[]
}

export default function CaseWorkspace() {
  const { id } = useParams()
  const persona = usePersona()
  const { data, error, reload } = useApi<Detail>(`/cases/${id}`, [persona?.id])
  const [note, setNote] = useState('')
  const [narr, setNarr] = useState('')
  const [msg, setMsg] = useState<string | null>(null)
  const [focus, setFocus] = useState<string[]>([])
  const [onlyAlerted, setOnlyAlerted] = useState(true)
  useEffect(() => { if (data) setNarr(data.case.narrative) }, [data?.case.id, data?.case.narrative]) // eslint-disable-line
  if (!data) return <Loading error={error} />
  const { case: c, party, agent_run: run } = data
  const act = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); setMsg(ok); setNote(''); reload() } catch (e) { setMsg((e as Error).message) }
  }
  const openStates = data.workflow.states.filter(s => s.kind === 'open')
  const idx = openStates.findIndex(s => s.id === c.state)
  const txns = data.transactions.filter(t => (focus.length ? focus.includes(t.id) : !onlyAlerted || t.alerted))
  const rec = run?.steps.find(s => s.agent === 'orchestrator')
  return (
    <>
      <div className="crumbs"><Link to="/cases">Cases</Link> / {c.id}</div>
      <div className="case-h">
        <div>
          <h1>{party.name}</h1>
          <p>{c.type_title} <span className="muted">v{c.type_version}</span> · opened {fmtDateTime(c.created)}</p>
        </div>
        <div className="case-meta">
          <Priority p={c.priority} />
          <span>{c.sla_clock}: <Sla days={daysUntil(c.sla_due)} open={c.open} /></span>
          <span>Owner: {c.owner ?? 'unassigned'}</span>
        </div>
      </div>
      <ol className="stepper" aria-label="Workflow stage">
        {openStates.map((s, i) => <li key={s.id} className={!c.open || i < idx ? 'done' : i === idx ? 'now' : ''}>{s.label}</li>)}
        <li className={c.open ? '' : 'now end'}>{c.open ? 'Decision' : c.state_label}</li>
      </ol>

      <div className="workspace">
        <div className="col">
          <Card title="Agent briefing" action={<button className="ghost" onClick={() => act(() => api.post(`/cases/${c.id}/agents/run`), 'Agent investigation re-run.')}>Re-run</button>}>
            {!run ? <p className="empty">No agent run yet for this case.</p> : <>
              <div className={`rec ${run.recommendation}`}>
                <div><span className="muted small">Recommendation · autonomy {data.workflow.autonomy}</span>
                  <b>{run.recommendation === 'escalate' ? 'Escalate' : run.recommendation === 'close' ? 'Close, not suspicious' : 'No recommendation'}</b></div>
                <div className="conf"><span className="muted small">Confidence</span><b>{Math.round(run.confidence * 100)}%</b></div>
              </div>
              {rec && <ul className="signals">{rec.findings.map((f, i) => <li key={i} className={f.text.startsWith('+') ? 'up' : 'down'}>{f.text.slice(2)}</li>)}</ul>}
              <p className="muted small">The agent prepares and recommends. Only a person can move, close or file this case.</p>
              {run.steps.filter(s => s.agent !== 'orchestrator' && s.agent !== 'narrative_writer').map(s => (
                <details key={s.agent} open={s.agent === 'typology_analyst' || s.agent === 'sanctions_adjudicator'}>
                  <summary><b>{s.title}</b><span className="muted"> {s.summary}</span></summary>
                  <ul className="findings">{s.findings.map((f, i) => (
                    <li key={i}>{f.text}{f.cites.length > 0 && <button className="cite" onClick={() => setFocus(f.cites)}>{f.cites.length} source{f.cites.length > 1 ? 's' : ''}</button>}</li>))}</ul>
                </details>))}
              <p className="muted small">Provider: {run.provider}. Every finding is computed from case data.</p>
            </>}
          </Card>
          <Card title={`Alerts (${data.alerts.length})`}>
            {data.alerts.length === 0 && <p className="empty">No alerts. This case was opened on a schedule.</p>}
            {data.alerts.map(a => (
              <div className="alert" key={a.id}>
                <div><b>{a.title}</b> <Pill>{a.typology}</Pill><span className="score">score {a.score.toFixed(2)}</span></div>
                <p>{a.explanation}</p>
                <div className="muted small">{a.id} · scenario v{a.version} · {a.source} · <button className="cite" onClick={() => setFocus(a.txn_ids)}>show {a.txn_ids.length} transactions</button></div>
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
            {data.filing && <div className="filed"><b>SAR filed</b> by {data.filing.filed_by} on {fmtDateTime(data.filing.filed_at)}<code>{data.filing.id} · sha256 {data.filing.sha256.slice(0, 16)}…</code></div>}
            {!c.open && !data.filing && <p className="empty">Closed: {c.state_label}.</p>}
            {c.open && <>
              <label className="lbl" htmlFor="note">Rationale note</label>
              <textarea id="note" rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="Required for closures and returns" />
              <div className="actions">{data.transitions.map(t => (
                <button key={t.to} disabled={!!t.blocked || (t.needs_note && !note.trim())} title={t.blocked ?? (t.needs_note && !note.trim() ? 'Add a rationale note first' : '')}
                  className={/file|block/i.test(t.label) ? 'primary' : ''}
                  onClick={() => act(() => api.post(`/cases/${c.id}/transition`, { to: t.to, note }), `Moved to ${t.to_label}.`)}>
                  {t.label}{t.blocked && <small>{t.blocked}</small>}
                </button>))}
                {data.transitions.length === 0 && <p className="empty">No actions from this stage.</p>}
              </div>
              <p className="muted small">Acting as {persona?.name} ({persona?.title}). Switch persona at the top to see role and four-eyes controls.</p>
            </>}
            {msg && <p className="msg" role="status">{msg}</p>}
          </Card>
          <Card title="Narrative" action={<span className="muted small">{c.drafted_by ? `Saved by ${c.drafted_by}` : narr ? 'Agent draft, not yet reviewed' : ''}</span>}>
            <textarea rows={11} value={narr} onChange={e => setNarr(e.target.value)} disabled={!c.open} aria-label="Narrative" />
            {c.open && <button onClick={() => act(() => api.post(`/cases/${c.id}/narrative`, { text: narr }), 'Narrative saved under your name.')} disabled={!narr.trim()}>Save as my narrative</button>}
          </Card>
          <Card title="Audit trail">
            <ul className="events tight">{data.events.map(e => (
              <li key={e.id}><Pill tone={e.actor_kind === 'agent' ? 'agent' : e.actor_kind === 'human' ? 'human' : 'neutral'}>{e.actor_kind}</Pill>
                <span><b>{e.kind}</b> · {e.actor}{typeof e.payload.note === 'string' && e.payload.note ? <em> “{e.payload.note}”</em> : null}</span>
                <time>{fmtDateTime(e.ts)}</time></li>))}</ul>
          </Card>
        </div>
      </div>
    </>
  )
}
