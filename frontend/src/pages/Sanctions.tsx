import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmtDate, fmtDateTime, fmtMoney, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../shell/people'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import { HoldRow, type Hold } from '../components/HoldsCard'
import { EX_TONE } from '../components/exemptionTone'
import { type Exemption } from '../components/ScreeningCard'
import { ListCard } from '../components/sanctions/lists'
import type { List } from '../components/sanctions/types'
import PageTabs from '../components/layout/PageTabs'

type Request = {
  id: string; reference: string; received: string; due: string; status: string; subjects_count: number; overdue: boolean; possible: number; pending: number; confirmed: number
  searched_at: string | null; responded_at: string | null; responded_by: string | null; response: null | { positive: boolean; late: boolean; subjects: { no: number; name: string; customers: number; transactions: number }[] }
  scope: { customers?: number; counterparty_names?: number; transactions_since?: string; match_at?: number }
}
type Subject = { no: number; name: string; kind: string; aliases: string[]; dob: string; identifiers: string; country: string }
type Match = { id: string; subject_no: number; where: string; name: string; score: number; basis: string; party_ids: string[]; decision: string; decided_by: string | null; note: string }
type Detail = Request & { subjects: Subject[]; matches: Match[]; can_handle: boolean }
type Desk = {
  lists: List[]; can_manage: boolean; can_decide: boolean; can_report: boolean; me: string; exemptions: Exemption[]; holds: Hold[]
  reports_owed: { hold_id: string; case_id: string; party_id: string; party_name: string; kind: string; amount: number; decided_at: string; due: string; overdue: boolean }[]
  counts: { entries: number; held: number; held_amount: number; awaiting_second: number; exemptions_pending: number; exemptions_active: number; reports_owed: number; reports_overdue: number }
  requests: { allowed: boolean; can_handle: boolean; requests: Request[]; rules: Record<string, number> }
}
const METHODS = [{ id: 'call_back', label: 'Call back' }]
const REQ: Record<string, [string, string]> = { received: ['warning', 'Not yet searched'], searched: ['human', 'Searched, deciding matches'], responded: ['good', 'Answered: match reported'], no_match: ['neutral', 'Closed: nothing to report'] }

function RequestDetail({ id, onChange }: { id: string; onChange: (m: string) => void }) {
  const { data, reload } = useApi<Detail>(`/sanctions/requests/${id}`)
  const nameOf = useNameOf()
  const [notes, setNotes] = useState<Record<string, string>>({})
  const [err, setErr] = useState<string | null>(null)
  if (!data) return <p className="empty">Loading…</p>
  const act = (path: string, body: unknown, ok: string) => api.post(`/sanctions/requests/${id}${path}`, body).then(() => { setErr(null); reload(); onChange(ok) }).catch(e => setErr(e.message))
  const open = data.status === 'received' || data.status === 'searched'
  return (
    <div className="req-detail">
      <div className="cols-2">
        <div><h3>Subjects ({data.subjects.length})</h3>
          <table><tbody>{data.subjects.map(x => <tr key={x.no}><td>{x.no}</td><td><b>{x.name}</b>{x.aliases.length > 0 && <div className="muted small">also {x.aliases.join(', ')}</div>}</td>
            <td>{x.kind}</td><td className="small">{[x.dob && `born ${x.dob}`, x.country, x.identifiers].filter(Boolean).join(' · ')}</td></tr>)}</tbody></table></div>
        <div><h3>What was searched</h3>
          {data.searched_at ? <p className="small">{data.scope.customers} customers and {data.scope.counterparty_names} names on payments since {data.scope.transactions_since}, at {Math.round((data.scope.match_at ?? 0) * 100)}% name similarity or more.
            Searched {fmtDateTime(data.searched_at)}. Names only: dates of birth and identity numbers are for the person deciding.</p> : <p className="small muted">Not searched yet.</p>}
          {data.response && <p className="small"><b>{data.response.positive ? 'Match reported' : 'Nothing to report'}</b> by {nameOf(data.responded_by ?? '')}, {fmtDateTime(data.responded_at!)}{data.response.late && <Pill tone="critical">after the deadline</Pill>}
            {data.response.subjects.map(x => <span key={x.no}> · {x.name}: {x.customers} customer record{x.customers === 1 ? '' : 's'}, {x.transactions} on payments</span>)}</p>}
          {data.can_handle && open && <div className="inline">
            <button onClick={() => act('/search', {}, 'Search done.')}>{data.status === 'received' ? 'Search the records' : 'Search again'}</button>
            {data.status === 'searched' && <button className="primary" disabled={data.pending > 0} onClick={() => act('/respond', {}, 'Request closed.')}>{data.confirmed ? 'Record the answer' : 'Close: nothing to report'}</button>}
            {data.pending > 0 && <span className="muted small">{data.pending} possible match{data.pending === 1 ? '' : 'es'} to decide first.</span>}</div>}
        </div>
      </div>
      {data.matches.length > 0 && <><h3>Possible matches ({data.matches.length})</h3>
        <table><thead><tr><th>Subject</th><th>Found</th><th>Likeness</th><th>Where</th><th>Decision</th></tr></thead>
          <tbody>{data.matches.map(m => <tr key={m.id}>
            <td>{m.subject_no}. {data.subjects.find(x => x.no === m.subject_no)?.name}</td>
            <td><b>{m.name}</b><div className="muted small">{m.where === 'customer' ? m.party_ids.map(p => <Link key={p} to={`/customers/${p}`}>{p} </Link>) : m.basis}</div></td>
            <td>{Math.round(m.score * 100)}%</td><td>{m.where === 'customer' ? 'A customer' : 'Named on payments'}</td>
            <td>{m.decision !== 'pending' ? <><Pill tone={m.decision === 'confirmed' ? 'critical' : 'neutral'}>{m.decision === 'confirmed' ? 'Same party' : 'Not the same'}</Pill><div className="small">“{m.note}” {nameOf(m.decided_by ?? '')}</div></>
              : data.can_handle && data.status === 'searched' ? <div className="inline tight">
                <input type="search" aria-label={`Basis for ${m.name}`} placeholder="What it rests on" value={notes[m.id] ?? ''} onChange={e => setNotes({ ...notes, [m.id]: e.target.value })} />
                <button disabled={(notes[m.id] ?? '').trim().length < 5} onClick={() => act(`/matches/${m.id}`, { confirmed: true, note: notes[m.id] }, 'Recorded as the same party.')}>Same party</button>
                <button disabled={(notes[m.id] ?? '').trim().length < 5} onClick={() => act(`/matches/${m.id}`, { confirmed: false, note: notes[m.id] }, 'Recorded as not the same.')}>Not the same</button>
              </div> : <Pill tone="warning">Undecided</Pill>}</td>
          </tr>)}</tbody></table></>}
      {err && <p className="msg" role="alert">{err}</p>}
    </div>
  )
}

function Requests({ view, onChange }: { view: Desk['requests']; onChange: (m: string) => void }) {
  const [open, setOpen] = useState<string | null>(view.requests.find(r => r.status === 'searched' || r.status === 'received')?.id ?? null)
  const [form, setForm] = useState<null | { reference: string; text: string }>(null)
  const [err, setErr] = useState<string | null>(null)
  const receive = () => form && api.post('/sanctions/requests', { reference: form.reference, subjects: form.text.split('\n').map(l => l.split('|').map(x => x.trim())).filter(p => p[0])
    .map(([name, kind, dob, country]) => ({ name, kind, dob, country })) }).then(() => { setForm(null); setErr(null); onChange('Request recorded. Its deadline is running.') }).catch(e => setErr(e.message))
  return (
    <Card title="Searches for law enforcement" action={view.can_handle && !form ? <button onClick={() => setForm({ reference: '', text: '' })}>Record a request…</button> : undefined}>
      <p className="muted small">Confidential. Only people with the information sharing permission see this section. A match here opens no case and is not shown on the customer.
        Answer within {view.rules.response_days} days; customers and {view.rules.transaction_months} months of payments are searched.</p>
      {form && <div className="override-form">
        <input type="search" aria-label="Tracking number" placeholder="Tracking number on the request" value={form.reference} onChange={e => setForm({ ...form, reference: e.target.value })} />
        <textarea rows={4} aria-label="Subjects" placeholder={'One subject per line: name | person or business | date of birth | country\nOrla Vance | person | 1980-01-01 | IE'} value={form.text} onChange={e => setForm({ ...form, text: e.target.value })} />
        <div className="inline"><button className="primary" disabled={!form.reference.trim() || !form.text.trim()} onClick={receive}>Record</button><button onClick={() => setForm(null)}>Cancel</button></div>
        {err && <p className="msg" role="alert">{err}</p>}
      </div>}
      {view.requests.length === 0 ? <p className="empty">No requests on record.</p> : (
        <table><thead><tr><th>Request</th><th>Received</th><th>Due</th><th>Standing</th><th>Possible matches</th><th /></tr></thead>
          <tbody>{view.requests.map(r => <FragmentRow key={r.id} r={r} open={open === r.id} toggle={() => setOpen(open === r.id ? null : r.id)} onChange={onChange} />)}</tbody></table>)}
    </Card>
  )
}

function FragmentRow({ r, open, toggle, onChange }: { r: Request; open: boolean; toggle: () => void; onChange: (m: string) => void }) {
  return (
    <>
      <tr>
        <td><b>{r.reference}</b><div className="muted small">{r.subjects_count} subjects</div></td><td>{fmtDate(r.received)}</td>
        <td>{fmtDate(r.due)}{r.overdue && <Pill tone="critical">Overdue</Pill>}</td><td><Pill tone={REQ[r.status][0]}>{REQ[r.status][1]}</Pill></td>
        <td>{r.searched_at ? <>{r.possible} found{r.pending ? `, ${r.pending} undecided` : ''}{r.confirmed ? `, ${r.confirmed} confirmed` : ''}</> : <span className="muted">–</span>}</td>
        <td><button className="ghost" aria-expanded={open} onClick={toggle}>{open ? 'Close' : 'Open'}</button></td>
      </tr>
      {open && <tr className="doc-detail"><td colSpan={6}><RequestDetail id={r.id} onChange={onChange} /></td></tr>}
    </>
  )
}

/** The sanctions desk: what is held, what is owed to the authority, the lists in force, and the decisions that shape screening. */
export default function Sanctions() {
  const { data, error, reload } = useApi<Desk>('/sanctions')
  const { holder } = usePeople()
  const nameOf = useNameOf()
  const nav = useNavigate()
  const [msg, setMsg] = useState<string | null>(null)
  const [notes, setNotes] = useState<Record<string, string>>({})
  if (!data) return <Loading error={error} />
  const c = data.counts
  const done = (m: string) => { setMsg(m); reload() }
  const fail = (e: Error) => setMsg(e.message)
  const prepare = (holdId: string) => api.post<{ filing_id: string }>(`/holds/${holdId}/report`).then(r => nav(`/filings/${r.filing_id}`)).catch(fail)
  const decide = (x: Exemption, approve: boolean) => api.post(`/sanctions/exemptions/${x.id}/decision`, { approve, note: notes[x.id] ?? '' })
    .then(() => done(approve ? `Approved. '${x.subject}' will no longer be raised against ${x.entry_name}.` : 'Refused. Screening is unchanged.')).catch(fail)
  const revoke = (x: Exemption) => { const note = window.prompt(`Why end the exemption for ${x.subject}?`); if (note) api.post(`/sanctions/exemptions/${x.id}/revoke`, { note }).then(() => done('Exemption ended.')).catch(fail) }
  return (
    <>
      <div className="page-h"><h1>Sanctions</h1>
        <p>Payments and customers are screened against the lists in force. A possible match holds the payment; two people end a hold; what is blocked or sent back is reported to the authority. A change to any list screens everyone again.</p></div>
      <div className="kpis">
        <Kpi label="Payments held" value={c.held} sub={fmtMoney(c.held_amount)} bad={c.held > 0} to="/sanctions" />
        <Kpi label="Waiting for a second person" value={c.awaiting_second + c.exemptions_pending} sub={`${c.awaiting_second} hold${c.awaiting_second === 1 ? '' : 's'}, ${c.exemptions_pending} exemption${c.exemptions_pending === 1 ? '' : 's'}`} />
        <Kpi label="Reports owed to the authority" value={c.reports_owed} sub={c.reports_overdue ? `${c.reports_overdue} overdue` : 'none overdue'} bad={c.reports_overdue > 0} to="/sanctions?tab=reports" />
        <Kpi label="Names screened against" value={c.entries} sub={`${data.lists.filter(l => l.active).length} lists in force`} to="/sanctions?tab=lists" />
        <Kpi label="False-match exemptions in force" value={c.exemptions_active} to="/sanctions?tab=exemptions" />
      </div>
      {msg && <p className="notice" role="status">{msg}</p>}
      <PageTabs label="Sanctions desk" tabs={[
        { id: 'held', label: 'Held payments', count: data.holds.length, alert: c.awaiting_second > 0, content: <>
        <Card title={`Held now (${data.holds.length})`}>
          {data.holds.length === 0 ? <p className="empty">Nothing is held for sanctions on the cases you can see.</p>
            : data.holds.map(h => <div key={h.id}><div className="small"><Link to={`/cases/${h.case_id}`}>{h.case_id}</Link> · <Link to={`/customers/${h.party_id}`}>{h.party_id}</Link></div>
              <HoldRow h={h} view={{ can_work: false, can_decide: data.can_decide, me: data.me, methods: METHODS }} onChange={done} /></div>)}
        </Card>
        </> },
        { id: 'reports', label: 'Reports owed', count: data.reports_owed.length, alert: c.reports_overdue > 0, content: <>
        <Card title={`Reports owed to the authority (${data.reports_owed.length})`}>
          {data.reports_owed.length === 0 ? <p className="empty">Every blocked or rejected payment has its report.</p> : (
            <table><thead><tr><th>Customer</th><th>What happened</th><th>Report due</th><th /></tr></thead>
              <tbody>{data.reports_owed.map(o => <tr key={o.hold_id}>
                <td><Link to={`/customers/${o.party_id}`}><b>{o.party_name}</b></Link><div className="muted small"><Link to={`/cases/${o.case_id}`}>{o.case_id}</Link></div></td>
                <td>{fmtMoney(o.amount)} {o.kind === 'BLOCK' ? 'blocked' : 'rejected and sent back'}<div className="muted small">{fmtDate(o.decided_at)}</div></td>
                <td><Pill tone={o.overdue ? 'critical' : 'warning'}>{o.overdue ? 'Overdue since' : 'By'} {fmtDate(o.due)}</Pill></td>
                <td>{data.can_report ? <button onClick={() => prepare(o.hold_id)}>Prepare report</button> : <span className="muted small">{holder('submit_filings')}</span>}</td>
              </tr>)}</tbody></table>)}
          <p className="muted small">Prepared reports are checked and sent from <Link to="/filings">Reports</Link>.</p>
        </Card>
        </> },
        { id: 'exemptions', label: 'False-match exemptions', count: c.exemptions_pending, alert: c.exemptions_pending > 0, content: <>
      <Card title="False-match exemptions">
        <p className="muted small">A decision that one name is not one list entry, so the same false match is not raised again. Two people, an end date, and it ends by itself if the list entry changes.</p>
        {data.exemptions.length === 0 ? <p className="empty">None on the cases you can see.</p> : (
          <div className="scroll"><table><thead><tr><th>Name screened</th><th>Is not</th><th>Why</th><th>Standing</th><th /></tr></thead>
            <tbody>{data.exemptions.map(x => <tr key={x.id}>
              <td><b>{x.subject}</b>{x.case_id && <div className="muted small"><Link to={`/cases/${x.case_id}`}>{x.case_id}</Link></div>}</td>
              <td>{x.entry_name}<div className="muted small">{x.entry_ref} · {x.list}</div></td>
              <td>“{x.reason}”<div className="muted small">{nameOf(x.proposed_by)}, {fmtDate(x.proposed_at)}</div></td>
              <td><Pill tone={EX_TONE[x.status]}>{words(x.status)}</Pill>{x.status === 'active' && x.expires && <div className="muted small">until {x.expires}, approved by {nameOf(x.decided_by ?? '')}</div>}
                {['refused', 'revoked'].includes(x.status) && x.decision_note && <div className="muted small">{x.decision_note}</div>}</td>
              <td>{x.status === 'pending' && (data.can_decide && x.proposed_by !== data.me ? <div className="inline tight">
                <input type="search" aria-label={`Note on ${x.subject}`} placeholder="Note (needed to refuse)" value={notes[x.id] ?? ''} onChange={e => setNotes({ ...notes, [x.id]: e.target.value })} />
                <button className="primary" onClick={() => decide(x, true)}>Approve</button><button disabled={!(notes[x.id] ?? '').trim()} onClick={() => decide(x, false)}>Refuse</button>
              </div> : <span className="muted small">{x.proposed_by === data.me ? 'You proposed this, so someone else decides.' : `${holder('sanctions_decisions')} can decide.`}</span>)}
                {x.status === 'active' && data.can_decide && <button className="ghost" onClick={() => revoke(x)}>End it</button>}</td>
            </tr>)}</tbody></table></div>)}
      </Card>
        </> },
        { id: 'requests', label: 'Law enforcement requests', hidden: !data.requests.allowed, count: data.requests.requests.filter(r => r.overdue).length, alert: true,
          content: <Requests view={data.requests} onChange={done} /> },
        { id: 'lists', label: 'Lists', content: <>
      <Card title="Lists screened against" action={data.can_manage ? <Link to="/screening-lists">Load or edit lists in Administration</Link> : undefined}>
        {data.lists.map(l => <ListCard key={l.code} l={l} canManage={false} onChange={done} />)}
        {!data.can_manage && <p className="muted small">{holder('manage_lists')} can load a new version or edit the client's own list.</p>}
      </Card>
        </> },
      ]} />
    </>
  )
}
