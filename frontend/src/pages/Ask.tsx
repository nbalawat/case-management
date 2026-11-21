import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, fmtDateTime, words } from '../api'
import { useApi } from '../useApi'
import { usePeople } from '../shell/people'
import { Card, Pill } from '../components/ui'

type Filter = { field: string; op: string; value: unknown }
type Breakdown = {
  columns: string[]; group_by: string[]; measures: string[]; groups: number; truncated: boolean; matching_cases: number; note?: string
  rows: { key: (string | number)[]; values: (number | null)[]; case_ids: string[] }[]
}
type CaseList = { columns: string[]; cases: Record<string, string | number | null>[]; matching_cases: number; truncated: boolean }
type TeamLoad = { people: { name: string; title: string; open_cases: number; past_stage_clock: number; closed_cases: number }[] }
type Step = { tool: string; input: { filters?: Filter[]; group_by?: string[]; measures?: string[]; sort_by?: string; case_id?: string; name_or_id?: string }; ok: boolean; summary: string; result?: unknown }
type Cited = { id: string; customer: string; case_type: string; stage: string; status: string }
type Answer = {
  id: string; conversation_id: string; question: string; answer: string; status: string; provider: string
  cited: Cited[]; unverified: string[]; steps: Step[]; duration_ms: number
}
type Scope = { client: string; person: string; title: string; team_workload: boolean; tools: string[] }
type Start = { suggestions: string[]; scope: Scope; provider: string }
type LogRow = { id: string; asked_by: string; asked_by_name: string; created: string; question: string; status: string; provider: string; steps: Step[]; cited: string[] }

const TOOL: Record<string, string> = {
  breakdown: 'Counted and compared cases', list_cases: 'Listed matching cases', case_detail: 'Read a case',
  customer: 'Looked up a customer', team_workload: 'Read team workload',
}
const PROVIDER = (p: string) => (p.startsWith('offline') ? 'Offline planner (no model connected)' : p.replace('anthropic:', 'Model: '))
const CASE = /(case-[0-9a-f]{10})/

/** Answer text is plain text with **bold**, "- " bullets and case ids. Nothing in it is ever treated as HTML. */
function inline(text: string, ok: Set<string>): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).flatMap((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) return [<b key={i}>{inline(part.slice(2, -2), ok)}</b>]
    return part.split(CASE).map((bit, j) => !/^case-[0-9a-f]{10}$/.test(bit) ? bit
      : ok.has(bit) ? <Link key={`${i}-${j}`} to={`/cases/${bit}`} className="caseref">{bit}</Link>
        : <span key={`${i}-${j}`} className="caseref bad" title="No lookup returned this case, so it is not linked">{bit}</span>)
  })
}

function AnswerText({ text, ok }: { text: string; ok: Set<string> }) {
  const blocks: ReactNode[] = []
  let list: string[] = []
  const flush = () => { if (list.length) { blocks.push(<ul key={blocks.length}>{list.map((l, i) => <li key={i}>{inline(l, ok)}</li>)}</ul>); list = [] } }
  for (const line of text.split('\n')) {
    if (/^\s*[-•]\s+/.test(line)) list.push(line.replace(/^\s*[-•]\s+/, ''))
    else { flush(); if (line.trim()) blocks.push(<p key={blocks.length} className={line.startsWith('Read as:') ? 'muted small' : ''}>{inline(line, ok)}</p>) }
  }
  flush()
  return <div className="answer-text">{blocks}</div>
}

const show = (v: unknown, measure = '') => v === null || v === undefined ? '–'
  : typeof v === 'number' ? (measure.includes('amount') ? `$${Math.round(v).toLocaleString('en-US')}` : measure === 'share' || measure === 'agent_agreement' ? `${v}%` : v.toLocaleString('en-US'))
    : String(v)

function Filters({ filters }: { filters?: Filter[] }) {
  if (!filters?.length) return <span className="muted small">All cases, no filters</span>
  return <>{filters.map((f, i) => <Pill key={i}>{words(f.field)} {f.op} {Array.isArray(f.value) ? f.value.join(' or ') : String(f.value)}</Pill>)}</>
}

function BreakdownView({ d }: { d: Breakdown }) {
  if (!d.group_by.length) return (
    <div className="kpis tight">{d.measures.map((m, i) => <div className="kpi" key={m}><span>{d.columns[i]}</span><b>{show(d.rows[0]?.values[i], m)}</b></div>)}</div>
  )
  const lead = d.measures[0]
  const max = Math.max(1, ...d.rows.map(r => Number(r.values[0] ?? 0)))
  if (d.group_by.length === 1) return (
    <>
      <div className="small muted bars-h"><span>{d.columns[0]}</span><span>{d.columns[1]}</span></div>
      {d.rows.map(r => (
        <div className="bar-row ask" key={r.key.join('|')} title={d.measures.map((m, i) => `${d.columns[i + 1]}: ${show(r.values[i], m)}`).join('\n')}>
          <span>{r.key[0]}</span>
          <div className="track"><div className="fill" style={{ width: `${(Number(r.values[0] ?? 0) / max) * 100}%`, visibility: r.values[0] ? 'visible' : 'hidden' }} /></div>
          <b>{show(r.values[0], lead)}</b>
          <span className="muted small">{d.measures.slice(1).map((m, i) => `${show(r.values[i + 1], m)} ${words(m).replace('avg', 'avg.')}`).join(' · ')}</span>
        </div>))}
      {d.note && <p className="muted small">{d.note}</p>}
    </>
  )
  return (
    <div className="scroll"><table>
      <thead><tr>{d.columns.map((c, i) => <th key={c} className={i >= d.group_by.length ? 'num' : ''}>{c}</th>)}</tr></thead>
      <tbody>{d.rows.map(r => <tr key={r.key.join('|')}>{r.key.map((k, i) => <td key={i}>{k}</td>)}{r.values.map((v, i) => <td key={i} className="num">{show(v, d.measures[i])}</td>)}</tr>)}</tbody>
    </table>{d.note && <p className="muted small">{d.note}</p>}</div>
  )
}

function ListView({ d }: { d: CaseList }) {
  return (
    <div className="scroll"><table>
      <thead><tr><th>Case</th><th>Customer</th><th>Type</th><th>Stage</th><th>Owner</th><th className="num">Alerted</th><th className="num">Age (days)</th></tr></thead>
      <tbody>{d.cases.map(c => (
        <tr key={String(c.id)}><td className="nowrap"><Link to={`/cases/${c.id}`}>{c.id}</Link></td><td>{c.customer}</td><td>{c.case_type}</td><td>{c.stage}</td>
          <td>{c.owner}</td><td className="num">{show(c.alerted_amount, 'amount')}</td><td className="num">{show(c.age_days)}</td></tr>))}</tbody>
    </table>{d.truncated && <p className="muted small">Showing {d.cases.length} of {d.matching_cases} matching cases.</p>}</div>
  )
}

function StepView({ step }: { step: Step }) {
  const r = step.result
  return (
    <div className="step">
      <div className="step-h"><b>{TOOL[step.tool] ?? step.tool}</b>
        {step.tool === 'case_detail' ? <Pill>{step.input.case_id}</Pill> : step.tool === 'customer' ? <Pill>{step.input.name_or_id}</Pill>
          : step.tool === 'team_workload' ? null : <Filters filters={step.input.filters} />}
        {!step.ok && <Pill tone="critical">not allowed or not possible</Pill>}</div>
      {!step.ok ? <p className="small">{step.summary}</p>
        : !r ? <p className="small muted">{step.summary}</p>
          : step.tool === 'breakdown' ? <BreakdownView d={r as Breakdown} />
            : step.tool === 'list_cases' ? <ListView d={r as CaseList} />
              : step.tool === 'team_workload' ? <BreakdownView d={{
                columns: ['Person', 'Open cases', 'Past the stage clock'], group_by: ['owner'], measures: ['cases', 'late'], groups: 0, truncated: false, matching_cases: 0,
                rows: (r as TeamLoad).people.map(p => ({ key: [p.name], values: [p.open_cases, p.past_stage_clock], case_ids: [] })) }} />
                : <p className="small muted">{step.summary}</p>}
    </div>
  )
}

function Turn({ a }: { a: Answer }) {
  const ok = new Set(a.cited.map(c => c.id))
  const good = a.steps.filter(s => s.ok)
  const main = [...good].reverse().find(s => s.tool === 'breakdown' || s.tool === 'list_cases' || s.tool === 'team_workload')
  const rest = a.steps.filter(s => s !== main)
  return (
    <article className="turn">
      <div className="q"><span>{a.question}</span></div>
      <div className={`a ${a.status}`}>
        {a.status !== 'answered' && <Pill tone={a.status === 'failed' ? 'critical' : 'warning'}>{a.status === 'failed' ? 'Could not reach the model' : a.status === 'refused' ? 'Declined' : 'Incomplete'}</Pill>}
        <AnswerText text={a.answer} ok={ok} />
        {a.unverified.length > 0 && <p className="verdict bad small">Not linked: {a.unverified.join(', ')}. No lookup returned {a.unverified.length === 1 ? 'this case' : 'these cases'}, so treat that part of the answer with care.</p>}
        {main && <div className="evidence"><StepView step={main} /></div>}
        {a.cited.length > 0 && <div className="cites"><span className="lbl">Cases this answer points to</span>
          {a.cited.map(c => <Link key={c.id} to={`/cases/${c.id}`} className="citecard"><b>{c.id}</b><span>{c.customer}</span><small>{c.case_type} · {c.stage}</small></Link>)}</div>}
        <details><summary>How this was answered <span className="muted small">· {a.steps.length} lookup{a.steps.length === 1 ? '' : 's'} · {PROVIDER(a.provider)} · {(a.duration_ms / 1000).toFixed(1)}s</span></summary>
          <p className="small muted">Every lookup ran as you, inside your client. The answer can only use what these lookups returned.</p>
          {main && <div className="step"><div className="step-h"><b>{TOOL[main.tool]}</b>{main.tool !== 'team_workload' && <Filters filters={main.input.filters} />}<span className="muted small">shown above</span></div></div>}
          {rest.map((s, i) => <StepView key={i} step={s} />)}
        </details>
      </div>
    </article>
  )
}

export default function Ask() {
  const { persona, can } = usePeople()
  const { data: start } = useApi<Start>('/ask/start')
  const { data: record, reload } = useApi<LogRow[]>('/ask/log')
  const [turns, setTurns] = useState<Answer[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [turns, busy])

  const ask = (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    setBusy(q); setError(null); setText('')
    api.post<Answer>('/ask', { question: q, conversation_id: turns[0]?.conversation_id })
      .then(a => { setTurns(t => [...t, a]); reload() }).catch(e => { setError(e.message); setText(q) }).finally(() => setBusy(null))
  }
  const submit = (e: FormEvent) => { e.preventDefault(); ask(text) }
  // A question handed over from the command palette (/ask?q=...) is asked once, then cleared from the address.
  const [params, setParams] = useSearchParams()
  const handed = params.get('q')
  const taken = useRef<string | null>(null)
  useEffect(() => {
    if (!handed || taken.current === handed) return
    taken.current = handed
    setParams({}, { replace: true })
    ask(handed)
  }, [handed]) // eslint-disable-line react-hooks/exhaustive-deps
  const offline = start?.provider === 'offline'
  return (
    <>
      <div className="page-h"><h1>Ask</h1>
        <p>Ask anything about the caseload in plain language. Answers come from live case data, name the cases behind them, and show how they were worked out.</p></div>
      <div className="ask">
        <section className="card thread">
          {turns.length === 0 && !busy && (
            <div className="ask-empty">
              <h2>What would you like to know{persona ? `, ${persona.name.split(' ')[0]}` : ''}?</h2>
              <div className="suggest">{start?.suggestions.map(s => <button key={s} onClick={() => ask(s)}>{s}</button>)}</div>
            </div>)}
          {turns.map(a => <Turn key={a.id} a={a} />)}
          {busy && <article className="turn"><div className="q"><span>{busy}</span></div><div className="a"><p className="muted thinking" role="status">Looking through the cases…</p></div></article>}
          <div ref={end} />
          {error && <p className="msg" role="alert">{error}</p>}
          <form className="ask-box" onSubmit={submit}>
            <input type="search" aria-label="Your question" placeholder={turns.length ? 'Ask a follow-up or a new question' : 'For example: which scenario produces the most SARs?'}
              value={text} onChange={e => setText(e.target.value)} maxLength={1000} autoFocus />
            <button className="primary" disabled={!text.trim() || !!busy}>Ask</button>
            {turns.length > 0 && <button type="button" onClick={() => setTurns([])}>New conversation</button>}
          </form>
        </section>
        <aside>
          {start && <Card title="What an answer can draw on">
            <dl className="facts">
              <dt>Client</dt><dd><b>{start.scope.client}</b> only</dd>
              <dt>Asking as</dt><dd>{start.scope.person}, {start.scope.title}</dd>
              <dt>Team workload</dt><dd>{start.scope.team_workload ? 'Included for your role' : 'Not included for your role'}</dd>
              <dt>Can change data</dt><dd>No. Asking only reads.</dd>
              <dt>Answered by</dt><dd>{PROVIDER(start.provider)}</dd>
            </dl>
            {offline && <p className="small muted">The offline planner matches common phrasing and answers one question at a time. Set <code>ANTHROPIC_API_KEY</code> before starting the app to answer free-form and follow-up questions with a model, through the same lookups.</p>}
          </Card>}
          <Card title={can('view_team') ? 'Question record · everyone' : 'Your recent questions'}>
            {!record?.length ? <p className="empty">No questions yet.</p> : <ul className="record">{record.slice(0, 12).map(r => (
              <li key={r.id}><button className="ghost" onClick={() => ask(r.question)} title="Ask this again">{r.question}</button>
                <small className="muted">{can('view_team') ? `${r.asked_by_name} · ` : ''}{fmtDateTime(r.created + 'Z')} · {r.steps.length} lookup{r.steps.length === 1 ? '' : 's'} · {r.cited.length} cited{r.status !== 'answered' ? ` · ${r.status}` : ''}</small></li>))}</ul>}
            <p className="small muted">Every question is recorded with who asked, what was looked up and what was answered.</p>
          </Card>
        </aside>
      </div>
    </>
  )
}
