import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api, fmtDateTime, words } from '../api'
import { useApi } from '../useApi'
import { usePeople } from '../shell/people'
import { Card, Pill } from '../components/ui'
import { providerLabel } from './ask/format'
import type { Answer, LogRow, Start, Step } from './ask/types'
import { mainStep, toolLabel } from './ask/steps'
import { AnswerText, Filters, type Links, StepView } from './ask/views'
import { PassageText } from '../components/PassageText'

/** Every id the answer may link, and where to: cases it cited and records a lookup showed or that the reader may open. */
const linksOf = (a: Answer): Links => new Map([
  ...a.cited.map(c => [c.id, `/cases/${c.id}`] as [string, string]),
  ...(a.sources ?? []).filter(s => s.link).map(s => [s.id, s.link] as [string, string]),
])

function Checks({ a }: { a: Answer }) {
  const ids = a.unverified ?? []
  const figures = a.figures?.unverified ?? []
  if (!ids.length && !figures.length) {
    return a.figures?.stated ? <p className="verdict good small">Every figure in this answer matches what a lookup returned.</p> : null
  }
  return (
    <div className="verdict bad small">
      {ids.length > 0 && <p>Not linked: {ids.join(', ')}. No lookup returned {ids.length === 1 ? 'this record' : 'these records'}, so treat that part of the answer with care.</p>}
      {figures.length > 0 && <p>Not checked: {figures.join(', ')}. {figures.length === 1 ? 'This figure does' : 'These figures do'} not match anything a lookup returned.</p>}
    </div>
  )
}

function Sources({ a }: { a: Answer }) {
  const others = (a.sources ?? []).filter(s => s.kind !== 'case' && s.kind !== 'policy')
  const policies = (a.sources ?? []).filter(s => s.kind === 'policy')
  if (!a.cited.length && !others.length && !policies.length) return null
  return (
    <>
      {(a.cited.length > 0 || others.length > 0) && <div className="cites"><span className="lbl">Records this answer points to</span>
        {a.cited.map(c => <Link key={c.id} to={`/cases/${c.id}`} className="citecard"><b>{c.id}</b><span>{c.customer}</span><small>{c.case_type} · {c.stage}</small></Link>)}
        {others.map(s => <Link key={s.id} to={s.link || '#'} className="citecard"><b>{s.id}</b><span>{words(s.kind)}</span><small>{s.label}</small></Link>)}
      </div>}
      {policies.length > 0 && <div className="cites"><span className="lbl">Policy and guidance it rests on</span>
        {policies.map(s => (
          <details key={s.id} className="citecard policycard"><summary><b>{s.id}</b> <span>{s.label}</span>{s.client_document && <small> · this client's own</small>}</summary>
            {s.excerpt && <PassageText text={s.excerpt} />}
            <Link to={s.link} className="small">Open in the library</Link>
          </details>
        ))}
      </div>}
    </>
  )
}

function Turn({ a }: { a: Answer }) {
  const links = linksOf(a)
  const main = mainStep(a.steps)
  const rest = a.steps.filter(s => s !== main)
  return (
    <article className="turn">
      <div className="q"><span>{a.question}</span></div>
      <div className={`a ${a.status}`}>
        {a.status !== 'answered' && <Pill tone={a.status === 'failed' ? 'critical' : 'warning'}>{a.status === 'failed' ? 'Could not reach the model' : a.status === 'refused' ? 'Declined' : 'Incomplete'}</Pill>}
        <AnswerText text={a.answer} links={links} />
        <Checks a={a} />
        {main && <div className="evidence"><StepView step={main} links={links} /></div>}
        <Sources a={a} />
        <details><summary>How this was answered <span className="muted small">· {a.steps.length} lookup{a.steps.length === 1 ? '' : 's'} · {providerLabel(a.provider)} · {(a.duration_ms / 1000).toFixed(1)}s</span></summary>
          <p className="small muted">Every lookup ran as you, inside your client. The answer can only use what these lookups returned, and its figures and record ids are checked against them.</p>
          {a.routed && <p className="small muted">{a.routed}</p>}
          {main && <div className="step"><div className="step-h"><b>{toolLabel(main)}</b>{main.tool !== 'team_workload' && <Filters filters={main.input.filters} object={main.input.object} />}<span className="muted small">shown above</span></div></div>}
          {rest.map((s, i) => <StepView key={i} step={s} links={links} />)}
        </details>
      </div>
    </article>
  )
}

/** A question in flight: each lookup appears as it finishes. */
function Working({ question, steps }: { question: string; steps: Step[] }) {
  return (
    <article className="turn"><div className="q"><span>{question}</span></div>
      <div className="a">
        {steps.map((s, i) => <p key={i} className="small">{s.ok ? '✓' : '✕'} {toolLabel(s)} <span className="muted">· {s.summary}</span></p>)}
        <p className="muted thinking" role="status">{steps.length ? 'Working on the answer…' : 'Looking through the records…'}</p>
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
  const [live, setLive] = useState<Step[]>([])
  const [error, setError] = useState<string | null>(null)
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [turns, busy, live])

  const ask = (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    setBusy(q); setLive([]); setError(null); setText('')
    let done = false
    api.stream('/ask/stream', { question: q, conversation_id: turns[0]?.conversation_id }, (event, data) => {
      if (event === 'step') setLive(l => [...l, data as Step])
      else if (event === 'done') { done = true; setTurns(t => [...t, data as Answer]); reload() }
      else if (event === 'error') throw new Error((data as { error: string }).error)
    }).then(() => { if (!done) throw new Error('The answer stopped before it finished. Try again.') })
      .catch(e => { setError(e.message); setText(q) }).finally(() => { setBusy(null); setLive([]) })
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
  const objects = (start?.scope.objects ?? ['cases']).map(words)
  return (
    <>
      <div className="page-h"><h1>Ask</h1>
        <p>Ask anything about your client's work in plain language: cases, transactions, alerts, filings, holds, disputes, KYC and the agents. Answers come from live data, name the records behind them, and show how they were worked out.</p></div>
      <div className="ask">
        <section className="card thread">
          {turns.length === 0 && !busy && (
            <div className="ask-empty">
              <h2>What would you like to know{persona ? `, ${persona.name.split(' ')[0]}` : ''}?</h2>
              <div className="suggest">{start?.suggestions.map(s => <button key={s} onClick={() => ask(s)}>{s}</button>)}</div>
            </div>)}
          {turns.map(a => <Turn key={a.id} a={a} />)}
          {busy && <Working question={busy} steps={live} />}
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
              <dt>Records</dt><dd>{objects.join(', ')}, as far as your role can see them</dd>
              <dt>Team workload</dt><dd>{start.scope.team_workload ? 'Included for your role' : 'Not included for your role'}</dd>
              <dt>Can change data</dt><dd>No. Asking only reads.</dd>
              <dt>Answered by</dt><dd>{providerLabel(start.provider)}</dd>
            </dl>
            {start.model?.reason && <p className="small muted">Model chosen by: {start.model.reason}.</p>}
            {offline && <p className="small muted">The offline planner matches common phrasing and answers one question at a time. Put <code>ANTHROPIC_API_KEY</code> (or an open-weight model's <code>OPEN_MODEL_URL</code>) in the repository's <code>.env</code> to answer free-form and follow-up questions with a model, through the same lookups.</p>}
          </Card>}
          <Card title={can('view_team') ? 'Question record · everyone' : 'Your recent questions'}>
            {!record?.length ? <p className="empty">No questions yet.</p> : <ul className="record">{record.slice(0, 12).map(r => (
              <li key={r.id}><button className="ghost" onClick={() => ask(r.question)} title="Ask this again">{r.question}</button>
                <small className="muted">{can('view_team') ? `${r.asked_by_name} · ` : ''}{fmtDateTime(r.created + 'Z')} · {r.steps.length} lookup{r.steps.length === 1 ? '' : 's'} · {r.cited.length} cited{r.figures?.unverified?.length ? ` · ${r.figures.unverified.length} figure(s) not checked` : ''}{r.status !== 'answered' ? ` · ${r.status}` : ''}</small></li>))}</ul>}
            <p className="small muted">Every question is recorded with who asked, what was looked up and what was answered.</p>
          </Card>
        </aside>
      </div>
    </>
  )
}
