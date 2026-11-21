import { useState } from 'react'
import { api, fmtDateTime, words } from '../../api'
import { useNameOf, usePersona } from '../../shell/people'
import { useApi } from '../../useApi'
import { Card, Pill } from '../ui'

export type Identifier = { kind: string; label: string; value: string; masked: boolean; issuer_country: string; issued: string | null; expires: string | null; expired: boolean; source: string }
export type Identity = {
  born: string | null; age: number | null; nationality: string; segment: string; booking_jurisdiction: string | null; relationship_manager: string | null
  address: { line1: string; line2: string; city: string; region: string; postal_code: string; country: string; one_line: string }
  phone: string; email: string; identifiers: Identifier[]; missing: string[]
}
type Event = { at: string; by: string; kind: string; detail: Record<string, unknown> }
const KINDS: [string, string][] = [['ssn', 'SSN or ITIN'], ['tin', 'Tax id'], ['ein', 'EIN'], ['passport', 'Passport'], ['national_id', 'National id'],
  ['drivers_license', "Driver's licence"], ['alien_registration', 'Alien registration'], ['company_registration', 'Company registration'], ['lei', 'LEI']]
const SEGMENTS = ['retail', 'business', 'private', 'correspondent', 'wealth', 'institutional']
const EVENT: Record<string, string> = { 'identity.updated': 'Corrected the record', 'identifier.added': 'Added a number', 'identifier.removed': 'Removed a number', 'identifiers.revealed': 'Looked at the full numbers' }
type Form = { born: string; nationality: string; address_line1: string; address_line2: string; city: string; region: string; postal_code: string; phone: string; email: string; segment: string; reason: string }

const fmtDay = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' })

/** Who the customer is: birth or incorporation date, address, contact details and identifying numbers, with who changed what. */
export default function IdentityCard({ partyId, kind, identity, onChange }: { partyId: string; kind: string; identity: Identity; onChange: () => void }) {
  const me = usePersona()
  const nameOf = useNameOf()
  const history = useApi<{ events: Event[] }>(`/customers/${partyId}/history`)
  const [shown, setShown] = useState<Identifier[] | null>(null)
  const [why, setWhy] = useState<string | null>(null)
  const [form, setForm] = useState<Form | null>(null)
  const [adding, setAdding] = useState<null | { kind: string; value: string; issuer_country: string; expires: string }>(null)
  const [msg, setMsg] = useState<string | null>(null)
  const canEdit = !!me && !me.read_only && me.permissions.includes('work')
  const company = kind === 'company'
  const a = identity.address
  const ids = shown ?? identity.identifiers
  const done = (text: string) => { setMsg(text); onChange(); history.reload() }
  const fail = (e: Error) => setMsg(e.message)
  const edit = () => setForm({ born: identity.born ?? '', nationality: identity.nationality, address_line1: a.line1, address_line2: a.line2, city: a.city,
    region: a.region, postal_code: a.postal_code, phone: identity.phone, email: identity.email, segment: identity.segment, reason: '' })
  const save = () => {
    if (!form) return
    const { reason, ...changes } = form
    api.post(`/customers/${partyId}/identity`, { changes, reason }).then(() => { setForm(null); setShown(null); done('Saved. The change is on the record with your name.') }).catch(fail)
  }
  const reveal = () => api.post<Identity>(`/customers/${partyId}/identifiers/reveal`, { reason: why })
    .then(r => { setShown(r.identifiers); setWhy(null); history.reload() }).catch(fail)
  const add = () => adding && api.post(`/customers/${partyId}/identifiers`, adding).then(() => { setAdding(null); setShown(null); done('Number added.') }).catch(fail)
  const field = (k: keyof Form, label: string, type = 'text') => form && (
    <label className="field"><span>{label}</span><input type={type} value={form[k]} onChange={e => setForm({ ...form, [k]: e.target.value })} /></label>)
  return (
    <Card title="Identity and contact" action={canEdit && !form && <button onClick={edit}>Correct…</button>}>
      {identity.missing.length > 0 && <div className="notice">Missing from this record: {identity.missing.join(', ')}. Regulatory reports about this customer will say these are unknown.</div>}
      {!form ? <dl className="facts">
        <dt>{company ? 'Incorporated' : 'Date of birth'}</dt><dd>{identity.born ? <>{fmtDay(identity.born)}{identity.age !== null && <span className="muted"> · age {identity.age}</span>}</> : <span className="muted">Not known</span>}</dd>
        <dt>{company ? 'Country of incorporation' : 'Nationality'}</dt><dd>{identity.nationality || <span className="muted">Not known</span>}</dd>
        <dt>Address</dt><dd>{a.one_line || <span className="muted">Not known</span>}</dd>
        <dt>Phone</dt><dd>{identity.phone || <span className="muted">–</span>}</dd>
        <dt>Email</dt><dd>{identity.email || <span className="muted">–</span>}</dd>
        <dt>Segment</dt><dd>{words(identity.segment)}{identity.booking_jurisdiction && <span className="muted"> · booked in {identity.booking_jurisdiction}</span>}</dd>
        {identity.relationship_manager && <><dt>Relationship manager</dt><dd>{nameOf(identity.relationship_manager)}</dd></>}
      </dl> : <div className="form-grid">
        {field('born', company ? 'Incorporated (YYYY-MM-DD)' : 'Date of birth (YYYY-MM-DD)')}
        {field('nationality', company ? 'Country of incorporation' : 'Nationality')}
        {field('address_line1', 'Street address')}{field('address_line2', 'Address line 2')}
        {field('city', 'City')}{field('region', 'State or region')}{field('postal_code', 'Postal code')}
        {field('phone', 'Phone', 'tel')}{field('email', 'Email', 'email')}
        <label className="field"><span>Segment</span><select value={form.segment} onChange={e => setForm({ ...form, segment: e.target.value })}>
          {SEGMENTS.map(s => <option key={s} value={s}>{words(s)}</option>)}</select></label>
        <label className="field wide"><span>Why is this changing?</span><input value={form.reason} placeholder="For example: customer sent a new proof of address" onChange={e => setForm({ ...form, reason: e.target.value })} /></label>
        <div className="inline"><button className="primary" disabled={!form.reason.trim()} onClick={save}>Save</button><button onClick={() => setForm(null)}>Cancel</button></div>
      </div>}
      <h4>Identifying numbers</h4>
      {ids.length === 0 ? <p className="empty">No identifying number on record.</p> : (
        <table className="rows"><thead><tr><th>Kind</th><th>Number</th><th>Issued by</th><th>Expires</th><th>From</th></tr></thead>
          <tbody>{ids.map(i => <tr key={`${i.kind}-${i.value}`}>
            <td>{i.label}</td><td className="mono">{i.value}</td><td>{i.issuer_country || '–'}</td>
            <td>{i.expires ? <>{fmtDay(i.expires)}{i.expired && <Pill tone="critical">expired</Pill>}</> : '–'}</td>
            <td className="muted small">{i.source}</td></tr>)}</tbody></table>)}
      <div className="inline">
        {canEdit && identity.identifiers.some(i => i.masked) && !shown && why === null && <button className="ghost" onClick={() => setWhy('')}>Show full numbers…</button>}
        {why !== null && <><input aria-label="Why you need the full numbers" placeholder="Why do you need the full numbers?" value={why} onChange={e => setWhy(e.target.value)} />
          <button className="primary" disabled={!why.trim()} onClick={reveal}>Show</button><button onClick={() => setWhy(null)}>Cancel</button></>}
        {shown && <><span className="muted small">Your look at the full numbers is recorded.</span><button className="ghost" onClick={() => setShown(null)}>Hide</button></>}
        {canEdit && !adding && <button className="ghost" onClick={() => setAdding({ kind: company ? 'company_registration' : 'passport', value: '', issuer_country: '', expires: '' })}>Add a number…</button>}
      </div>
      {adding && <div className="inline">
        <select aria-label="Kind of number" value={adding.kind} onChange={e => setAdding({ ...adding, kind: e.target.value })}>{KINDS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        <input aria-label="Number" placeholder="Number" value={adding.value} onChange={e => setAdding({ ...adding, value: e.target.value })} />
        <input aria-label="Issuing country" placeholder="Country (2 letters)" maxLength={2} size={4} value={adding.issuer_country} onChange={e => setAdding({ ...adding, issuer_country: e.target.value })} />
        <input aria-label="Expires" type="date" value={adding.expires} onChange={e => setAdding({ ...adding, expires: e.target.value })} />
        <button className="primary" disabled={!adding.value.trim()} onClick={add}>Add</button><button onClick={() => setAdding(null)}>Cancel</button>
      </div>}
      {msg && <p className="msg" role="status">{msg}</p>}
      {history.data && history.data.events.length > 0 && <details><summary className="small">{history.data.events.length} changes and look-ups on record</summary>
        <ul className="small">{history.data.events.map((e, n) => <li key={n}>{fmtDateTime(e.at)} · {nameOf(e.by)} · {EVENT[e.kind] ?? e.kind}
          {e.kind === 'identity.updated' && <> ({Object.keys(e.detail.changes as object).map(words).join(', ')})</>}
          {typeof e.detail.reason === 'string' && e.detail.reason && <>: “{e.detail.reason}”</>}</li>)}</ul></details>}
    </Card>
  )
}
