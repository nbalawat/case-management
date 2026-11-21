import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, fmtDate, fmtDateTime, fmtMoney, words } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../App'
import { Card, Kpi, Loading, Pill } from '../components/ui'
import { HoldRow, type Hold } from '../components/HoldsCard'
import { EX_TONE, type Exemption } from '../components/ScreeningCard'

type Entry = { id: string; ref: string; name: string; aliases: string[]; kind: string; countries: string[]; programs: string[]; added_version: number; removed_version: number | null }
type List = {
  code: string; name: string; kind: string; source: string; version: number; active: boolean; updated_by: string; updated_at: string; entries: number; removed: number
  history: { version: number; at: string; by: string; added: number; changed: number; removed: number; note: string }[]; rows: Entry[]; more: number
}
type Request = {
  id: string; reference: string; received: string; due: string; status: string; subjects_count: number; overdue: boolean; possible: number; pending: number; confirmed: number
  searched_at: string | null; responded_at: string | null; responded_by: string | null; response: null | { positive: boolean; late: boolean; subjects: { no: number; name: string; customers: number; transactions: number }[] }
  scope: { customers?: number; counterparty_names?: number; transactions_since?: string; match_at?: number }
}
type Subject = { no: number; name: string; kind: string; aliases: string[]; dob: string; identifiers: string; country: string }
type Match = { id: string; subject_no: number; where: string; name: string; score: number; basis: string; party_ids: string[]; decision: string; decided_by: string | null; note: string }
type Detail = Request & { subjects: Subject[]; matches: Match[]; can_handle: boolean }
type Rescreen = { customers_screened: number; customer_hits: number; payment_hits: number; cases: string[] }
type Desk = {
  lists: List[]; can_manage: boolean; can_decide: boolean; can_report: boolean; me: string; exemptions: Exemption[]; holds: Hold[]
  reports_owed: { hold_id: string; case_id: string; party_id: string; party_name: string; kind: string; amount: number; decided_at: string; due: string; overdue: boolean }[]
  counts: { entries: number; held: number; held_amount: number; awaiting_second: number; exemptions_pending: number; exemptions_active: number; reports_owed: number; reports_overdue: number }
  requests: { allowed: boolean; can_handle: boolean; requests: Request[]; rules: Record<string, number> }
}
const METHODS = [{ id: 'call_back', label: 'Call back' }]
const REQ: Record<string, [string, string]> = { received: ['warning', 'Not yet searched'], searched: ['human', 'Searched, deciding matches'], responded: ['good', 'Answered: match reported'], no_match: ['neutral', 'Closed: nothing to report'] }
const rescreened = (r: Rescreen | null) => (r ? ` Rescreened ${r.customers_screened} customers: ${r.customer_hits} customer and ${r.payment_hits} payment hits, ${r.cases.length} case${r.cases.length === 1 ? '' : 's'} opened or added to.` : '')

function ListCard({ l, canManage, onChange }: { l: List; canManage: boolean; onChange: (m: string) => void }) {
  const nameOf = useNameOf()
  const [add, setAdd] = useState<null | { ref: string; name: string; countries: string }>(null)
  const [err, setErr] = useState<string | null>(null)
  const run = (p: Promise<{ rescreen?: Rescreen | null }>, ok: string) => p.then(r => { setAdd(null); setErr(null); onChange(ok + rescreened(r.rescreen ?? null)) }).catch(e => setErr(e.message))
  const toggle = () => { const note = window.prompt(l.active ? 'Why stop screening against this list?' : 'Why screen against this list again?'); if (note) run(api.post(`/sanctions/lists/${l.code}/active`, { active: !l.active, note }), l.active ? 'List switched off.' : 'List switched on.') }
  const remove = (e: Entry) => { const note = window.prompt(`Why remove ${e.name}?`); if (note) run(api.post(`/sanctions/lists/${l.code}/entries/${encodeURIComponent(e.ref)}/remove`, { note }), `${e.name} removed. It stays in the history.`) }
  return (
    <div className="wlist">
      <div className="hold-h"><b>{l.name}</b><Pill tone={l.kind === 'internal' ? 'human' : 'neutral'}>{l.kind === 'internal' ? "The client's own" : 'Published'}</Pill>
        {l.active ? <Pill tone="good">Screening</Pill> : <Pill tone="serious">Switched off</Pill>}
        <span className="muted small">version {l.version} · {l.entries} entries{l.removed ? `, ${l.removed} removed` : ''} · {nameOf(l.updated_by) || l.updated_by}, {fmtDate(l.updated_at)}</span>
        {canManage && <span className="push"><button className="ghost" onClick={toggle}>{l.active ? 'Switch off' : 'Switch on'}</button>
          {l.kind === 'internal' && !add && <button onClick={() => setAdd({ ref: '', name: '', countries: '' })}>Add a name…</button>}</span>}</div>
      {l.source && <div className="small muted">{l.source}</div>}
      {add && <div className="inline"><input type="search" aria-label="Reference" placeholder="Reference" value={add.ref} onChange={e => setAdd({ ...add, ref: e.target.value })} />
        <input type="search" aria-label="Name" placeholder="Name" value={add.name} onChange={e => setAdd({ ...add, name: e.target.value })} />
        <input type="search" aria-label="Countries" placeholder="Countries, e.g. PA; CY" value={add.countries} onChange={e => setAdd({ ...add, countries: e.target.value })} />
        <button className="primary" disabled={!add.ref.trim() || !add.name.trim()} onClick={() => run(api.put(`/sanctions/lists/${l.code}/entries`, { entry: add }), `${add.name} added.`)}>Add and rescreen</button>
        <button onClick={() => setAdd(null)}>Cancel</button></div>}
      {err && <p className="msg" role="alert">{err}</p>}
      <details><summary className="small">Entries and history</summary>
        <div className="scroll tall short"><table><thead><tr><th>Reference</th><th>Name</th><th>Countries</th><th>Programme</th><th>Since</th><th /></tr></thead>
          <tbody>{l.rows.map(e => <tr key={e.id} className={e.removed_version ? 'gone' : ''}>
            <td>{e.ref}</td><td><b>{e.name}</b>{e.aliases.length > 0 && <div className="muted small">also {e.aliases.join(', ')}</div>}</td><td>{e.countries.join(', ')}</td><td>{e.programs.join(', ')}</td>
            <td>v{e.added_version}{e.removed_version ? `, removed in v${e.removed_version}` : ''}</td>
            <td>{canManage && l.kind === 'internal' && !e.removed_version && <button className="ghost" onClick={() => remove(e)}>Remove</button>}</td></tr>)}</tbody></table></div>
        {l.more > 0 && <p className="muted small">And {l.more} more.</p>}
        <ol className="custody">{l.history.map((x, i) => <li key={i}><b>Version {x.version}</b> by {nameOf(x.by) || x.by}, {fmtDateTime(x.at)}: {x.added} added, {x.changed} changed, {x.removed} removed{x.note ? `. ${x.note}` : ''}</li>)}</ol>
      </details>
    </div>
  )
}

function LoadList({ lists, onChange }: { lists: List[]; onChange: (m: string) => void }) {
  const [code, setCode] = useState(lists.find(l => l.kind === 'sanctions')?.code ?? '')
  const [fresh, setFresh] = useState({ code: '', name: '', kind: 'sanctions' })
  const [text, setText] = useState('')
  const [note, setNote] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const isNew = code === ''
  const read = (f: File | undefined) => f?.text().then(setText)
  const load = () => api.post<{ version: number; added: string[]; changed: string[]; removed: string[]; exemptions_ended: number; unchanged: boolean; rescreen: Rescreen | null }>(
    '/sanctions/lists', { ...(isNew ? fresh : { code }), text, note })
    .then(r => { setText(''); setNote(''); setErr(null); onChange(r.unchanged ? 'That is the version already held. Nothing changed.'
      : `Version ${r.version} loaded: ${r.added.length} added, ${r.changed.length} changed, ${r.removed.length} removed${r.exemptions_ended ? `, ${r.exemptions_ended} exemption(s) ended` : ''}.${rescreened(r.rescreen)}`) })
    .catch(e => setErr(e.message))
  return (
    <div className="override-form">
      <h3>Load a list version</h3>
      <div className="inline wrap">
        <select aria-label="List" value={code} onChange={e => setCode(e.target.value)}>{lists.map(l => <option key={l.code} value={l.code}>{l.name}</option>)}<option value="">A new list…</option></select>
        {isNew && <><input type="search" aria-label="Code" placeholder="Code, e.g. eu_consolidated" value={fresh.code} onChange={e => setFresh({ ...fresh, code: e.target.value })} />
          <input type="search" aria-label="Name" placeholder="Name" value={fresh.name} onChange={e => setFresh({ ...fresh, name: e.target.value })} />
          <select aria-label="Kind" value={fresh.kind} onChange={e => setFresh({ ...fresh, kind: e.target.value })}><option value="sanctions">Published sanctions list</option><option value="internal">The client's own list</option></select></>}
        <input type="file" aria-label="File" accept=".csv,.json,text/csv,application/json" onChange={e => read(e.target.files?.[0])} />
      </div>
      <textarea rows={4} aria-label="List contents" placeholder={'The list as issued: CSV with a header row (ref,name,aliases,kind,countries,programs,identifiers) or JSON.\nref,name,aliases,countries\nX-1,Orion Freight,Orion; OF Ltd,PA; CY'} value={text} onChange={e => setText(e.target.value)} />
      <div className="inline"><input type="search" aria-label="Note" placeholder="Note, such as the publisher's issue number" value={note} onChange={e => setNote(e.target.value)} />
        <button className="primary" disabled={!text.trim()} onClick={load}>Load and rescreen</button></div>
      <p className="muted small">The load is compared with what is held. Entries no longer on it are marked removed, never deleted, and everyone on the books is screened again.</p>
      {err && <p className="msg" role="alert">{err}</p>}
    </div>
  )
}

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
        <Kpi label="Payments held" value={c.held} sub={fmtMoney(c.held_amount)} bad={c.held > 0} />
        <Kpi label="Waiting for a second person" value={c.awaiting_second + c.exemptions_pending} sub={`${c.awaiting_second} hold${c.awaiting_second === 1 ? '' : 's'}, ${c.exemptions_pending} exemption${c.exemptions_pending === 1 ? '' : 's'}`} />
        <Kpi label="Reports owed to the authority" value={c.reports_owed} sub={c.reports_overdue ? `${c.reports_overdue} overdue` : 'none overdue'} bad={c.reports_overdue > 0} />
        <Kpi label="Names screened against" value={c.entries} sub={`${data.lists.filter(l => l.active).length} lists in force`} />
        <Kpi label="False-match exemptions in force" value={c.exemptions_active} />
      </div>
      {msg && <p className="notice" role="status">{msg}</p>}
      <div className="cols-2">
        <Card title={`Held now (${data.holds.length})`}>
          {data.holds.length === 0 ? <p className="empty">Nothing is held for sanctions on the cases you can see.</p>
            : data.holds.map(h => <div key={h.id}><div className="small"><Link to={`/cases/${h.case_id}`}>{h.case_id}</Link> · <Link to={`/customers/${h.party_id}`}>{h.party_id}</Link></div>
              <HoldRow h={h} view={{ can_work: false, can_decide: data.can_decide, me: data.me, methods: METHODS }} onChange={done} /></div>)}
        </Card>
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
      </div>
      <Card title="Lists screened against">
        {data.lists.map(l => <ListCard key={l.code} l={l} canManage={data.can_manage} onChange={done} />)}
        {data.can_manage ? <LoadList lists={data.lists} onChange={done} /> : <p className="muted small">{holder('manage_lists')} can load a new version or edit the client's own list.</p>}
      </Card>
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
      {data.requests.allowed && <Requests view={data.requests} onChange={done} />}
    </>
  )
}
