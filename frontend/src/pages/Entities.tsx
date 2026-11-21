import { useState } from 'react'
import { Link } from 'react-router-dom'
import { api, fmtDateTime, fmtMoney } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../shell/people'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import PageTabs from '../components/layout/PageTabs'
import { Flags } from './Customer'

type Ref = { kind: 'customer' | 'counterparty'; id: string }
type Question = { id: string; kind: string; question: string; left: string; right: string; score: number; left_ref: Ref; right_ref: Ref; reasons: string[]
  status: string; decided_by: string | null; decided_at: string | null; note: string }
type Desk = {
  can_decide: boolean; thresholds: { auto_merge_at: number; review_at: number }
  counts: { customers: number; counterparties: number; spellings: number; merged: number; open: number; decided: number }
  questions: Question[]; decided: Question[]
  merged: { id: string; name: string; aliases: { name: string; count: number; how: string }[]; flags: string[]; customers: number }[]
  flagged: { id: string; name: string; flags: string[]; customers: number; countries: string[]; total: number }[]
}
const pct = (n: number) => `${Math.round(n * 100)}%`
const to = (r: Ref) => (r.kind === 'customer' ? `/customers/${r.id}` : `/counterparties/${r.id}`)
const IF_SAME: Record<string, string> = {
  counterparty_variant: 'their payments are counted as one firm',
  customer_as_counterparty: 'payments to that name link the two customers directly',
  duplicate_customer: 'both records show a warning that they are one person',
}

export default function Entities() {
  const { data, error, reload } = useApi<Desk>('/entities')
  const { holder } = usePeople()
  const nameOf = useNameOf()
  const [kind, setKind] = useState('all')
  const [busy, setBusy] = useState<string | null>(null)
  const [fail, setFail] = useState<string | null>(null)
  if (!data) return <Loading error={error} />
  const decide = (q: Question, same: boolean) => {
    setBusy(q.id); setFail(null)
    api.post(`/entities/questions/${q.id}`, { same }).then(reload).catch(e => setFail(e.message)).finally(() => setBusy(null))
  }
  const kinds = [...new Map(data.questions.map(q => [q.kind, q.question])).entries()]
  const shown = data.questions.filter(q => kind === 'all' || q.kind === kind)
  const c = data.counts
  return (
    <>
      <div className="page-h"><h1>Entities</h1>
        <p>Who is who. Spellings of one firm are merged when they are at least {pct(data.thresholds.auto_merge_at)} alike. Between {pct(data.thresholds.review_at)} and that, a person decides. Whether a payee is one of your own customers, or two customer records are one person, is always a person's call.</p></div>
      <div className="kpis">
        <Kpi label="Customers" value={c.customers} />
        <Kpi label="Counterparties" value={c.counterparties} sub={`from ${c.spellings} spellings on payments`} />
        <Kpi label="Merged without a person" value={c.merged} sub="firms with more than one spelling" />
        <Kpi label="Waiting for a decision" value={c.open} bad={c.open > 0} />
        <Kpi label="Decided by people" value={c.decided} />
      </div>
      <PageTabs label="Entities" tabs={[
        { id: 'questions', label: 'Questions for a person', count: c.open, alert: c.open > 0, content: <>
      <Card title="Questions for a person" action={
        <select value={kind} onChange={e => setKind(e.target.value)} aria-label="Kind of question">
          <option value="all">All questions</option>{kinds.map(([k, label]) => <option key={k} value={k}>{label}</option>)}
        </select>}>
        {!data.can_decide && <p className="muted">You can read these but not decide them. {holder('resolve_entities')} can.</p>}
        {fail && <p className="notice">{fail}</p>}
        {shown.length === 0 ? <p className="empty">Nothing is waiting.</p> : (
          <div className="scroll"><table>
            <thead><tr><th>Question</th><th>This</th><th>And this</th><th className="num">Alike</th><th>Why it was raised</th>{data.can_decide && <th>Your decision</th>}</tr></thead>
            <tbody>{shown.map(q => (
              <tr key={q.id}>
                <td>{q.question}<div className="muted small">If yes, {IF_SAME[q.kind]}</div></td>
                <td><Link to={to(q.left_ref)}><b>{q.left}</b></Link></td><td><Link to={to(q.right_ref)}><b>{q.right}</b></Link></td>
                <td className="num">{pct(q.score)}</td><td>{q.reasons.join('. ')}</td>
                {data.can_decide && <td className="nowrap">
                  <button className="primary" disabled={busy === q.id} onClick={() => decide(q, true)}>Same</button>{' '}
                  <button disabled={busy === q.id} onClick={() => decide(q, false)}>Different</button></td>}
              </tr>))}</tbody>
          </table></div>)}
      </Card>
        </> },
        { id: 'merged', label: 'Merged automatically', count: data.merged.length, content: <>
        <Card title="Merged without a person">
          <table><thead><tr><th>Counterparty</th><th>Spellings found on payments</th><th className="num">Customers</th></tr></thead>
            <tbody>{data.merged.map(m => <tr key={m.id}><td><Link to={`/counterparties/${m.id}`}><b>{m.name}</b></Link><div><Flags flags={m.flags} /></div></td>
              <td>{m.aliases.map(a => <div key={a.name}>{a.name} <span className="muted small">{a.how === 'main spelling' ? '' : a.how}</span></div>)}</td><td className="num">{m.customers}</td></tr>)}</tbody></table>
        </Card>
        </> },
        { id: 'flagged', label: 'Flagged counterparties', count: data.flagged.length, content: <>
        <Card title="Flagged counterparties">
          <table><thead><tr><th>Counterparty</th><th>Countries</th><th className="num">Customers</th><th className="num">Total value</th></tr></thead>
            <tbody>{data.flagged.map(f => <tr key={f.id}><td><Link to={`/counterparties/${f.id}`}><b>{f.name}</b></Link><div><Flags flags={f.flags} /></div></td>
              <td>{f.countries.join(', ')}</td><td className="num">{f.customers}</td><td className="num">{fmtMoney(f.total)}</td></tr>)}</tbody></table>
        </Card>
        </> },
        { id: 'decided', label: 'Decisions on record', count: data.decided.length, content: <>
      <Card title="Decisions on record">{data.decided.length === 0 && <p className="empty">No decisions yet.</p>}
        <table><thead><tr><th>Question</th><th>This</th><th>And this</th><th>Decision</th><th>By</th><th>When</th></tr></thead>
          <tbody>{data.decided.map(q => <tr key={q.id}><td>{q.question}</td><td>{q.left}</td><td>{q.right}</td>
            <td><Pill tone={q.status === 'same' ? 'good' : 'neutral'}>{q.status === 'same' ? 'Same' : 'Different'}</Pill>{q.note}</td>
            <td>{nameOf(q.decided_by)}</td><td>{q.decided_at ? fmtDateTime(q.decided_at) : ''}</td></tr>)}</tbody></table>
        <p className="muted small">A decision is remembered by name, so it holds when new data arrives and the network is rebuilt.</p>
      </Card>
        </> },
      ]} />
    </>
  )
}
