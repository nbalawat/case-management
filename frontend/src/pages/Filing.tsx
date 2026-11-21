import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, fmtDate, fmtDateTime, fmtMoney } from '../api'
import { useApi } from '../useApi'
import { useNameOf } from '../shell/people'
import { Card, Loading } from '../components/ui'
import { Status, type FilingRow } from './Filings'

type Detail = FilingRow & { can_submit: boolean; document: string; intact: boolean; history: { at: string; by: string; what: string; reason?: string; reference?: string; channel?: string }[]
  report: { narrative: string } }
const WHAT: Record<string, string> = {
  prepared: 'Prepared', rechecked: 'Rebuilt and checked again', submitted: 'Sent', send_failed: 'Sending failed', acknowledged: 'Accepted by the regulator',
  rejected: 'Rejected by the regulator', superseded: 'Replaced by an amendment',
}

export default function Filing() {
  const { id } = useParams()
  const nav = useNavigate()
  const nameOf = useNameOf()
  const { data, error, reload } = useApi<Detail>(`/filings/${id}`)
  const [reference, setReference] = useState('')
  const [reason, setReason] = useState('')
  const [narrative, setNarrative] = useState<string | null>(null)
  const [fail, setFail] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  if (!data) return <Loading error={error} />
  const f = data
  const act = (path: string, body?: unknown, then?: (r: FilingRow) => void) => {
    setBusy(true); setFail(null)
    api.post<FilingRow>(`/filings/${f.id}/${path}`, body).then(r => (then ? then(r) : reload())).catch(e => setFail(e.message)).finally(() => setBusy(false))
  }
  const download = () => {
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([f.document], { type: 'application/xml' }))
    a.download = `${f.id}.xml`
    a.click()
    URL.revokeObjectURL(a.href)
  }
  return (
    <>
      <div className="page-h">
        <div className="crumbs"><Link to="/filings">Regulatory reports</Link> / {f.id}</div>
        <h1>{f.kind} on {f.subject}</h1>
        <p><Status f={f} /> {f.format_title} · prepared by {nameOf(f.prepared_by)} on {fmtDate(f.prepared_at)}
          {f.amount != null && <> · {fmtMoney(f.amount)}</>}
          {f.case_id && <> · <Link to={`/cases/${f.case_id}`}>Open the case</Link></>} · <Link to={`/customers/${f.party_id}`}>Customer profile</Link>
          {f.amends_id && <> · corrects <Link to={`/filings/${f.amends_id}`}>{f.amends_id}</Link></>}</p>
      </div>
      {!f.intact && <div className="notice">The stored document no longer matches the fingerprint taken when it was written. Treat it as altered.</div>}
      {fail && <div className="notice">{fail}</div>}
      <div className="cols-2">
        <Card title="Where it stands">
          {f.status === 'prepared' && (f.problems.length > 0 ? <>
            <p>This report cannot be sent until these are fixed:</p>
            <ul className="problems">{f.problems.map(p => <li key={p}>{p}</li>)}</ul>
            {f.can_submit && <button disabled={busy} onClick={() => act('recheck')}>Rebuild and check again</button>}
          </> : <>
            <p>The report passed every check and has not been sent.{f.due && <> It is due by {fmtDate(f.due)}.</>}</p>
            {f.can_submit && <button className="primary" disabled={busy} onClick={() => act('submit')}>Send to the regulator</button>}
          </>)}
          {f.status === 'submitted' && <>
            <p>Sent {f.submitted_at && fmtDateTime(f.submitted_at)}. {f.channel === 'outbox' ? <>Written to the outbox as <code>{f.receipt}</code>.</> : 'To be uploaded by hand on the regulator\'s site.'} Record the regulator's answer when it arrives.</p>
            {f.can_submit && <div className="stack">
              <label className="lbl">Accepted: the regulator's reference<input value={reference} onChange={e => setReference(e.target.value)} placeholder="For example a BSA ID" /></label>
              <button className="primary" disabled={busy || !reference.trim()} onClick={() => act('acknowledge', { reference })}>Record acceptance</button>
              <label className="lbl">Or rejected: what the regulator said<input value={reason} onChange={e => setReason(e.target.value)} /></label>
              <button disabled={busy || !reason.trim()} onClick={() => act('reject', { reason })}>Record rejection</button>
            </div>}
          </>}
          {f.status === 'acknowledged' && <p>Accepted {f.answered_at && fmtDate(f.answered_at)} under reference <b>{f.reference}</b>.{f.continuing_review_due && <> Look again by {fmtDate(f.continuing_review_due)} in case the activity has carried on.</>}</p>}
          {f.status === 'rejected' && <p>The regulator rejected this report: <b>{f.rejection}</b></p>}
          {f.status === 'superseded' && <p>This report was replaced by an amendment and is kept for the record.{f.rejection && <> It had been rejected: {f.rejection}</>}</p>}
          {f.can_submit && ['acknowledged', 'rejected'].includes(f.status) && (narrative === null
            ? <button disabled={busy} onClick={() => setNarrative(f.report.narrative ?? '')}>Amend this report</button>
            : <div className="stack">
              {f.case_id && <label className="lbl">Corrected narrative<textarea rows={8} value={narrative} onChange={e => setNarrative(e.target.value)} /></label>}
              <p className="muted small">The amendment is a new report that names this one. This one is kept unchanged.</p>
              <div><button className="primary" disabled={busy} onClick={() => act('amend', { narrative: f.case_id ? narrative : null }, r => { setNarrative(null); nav(`/filings/${r.id}`) })}>Prepare the amendment</button>{' '}
                <button onClick={() => setNarrative(null)}>Cancel</button></div>
            </div>)}
          <h3>History</h3>
          <table><tbody>{f.history.map((x, i) => <tr key={i}><td className="nowrap">{fmtDateTime(x.at)}</td><td>{WHAT[x.what] ?? x.what}{x.reference ? `: ${x.reference}` : ''}{x.reason ? `: ${x.reason}` : ''}</td><td>{nameOf(x.by)}</td></tr>)}</tbody></table>
        </Card>
        <Card title="The document" action={<button onClick={download}>Download</button>}>
          <p className="muted small">Exactly what {f.status === 'prepared' ? 'will be' : 'was'} sent. Fingerprint (SHA-256) {f.sha256.slice(0, 24)}… {f.intact ? 'matches.' : 'does not match.'}</p>
          <pre className="doc">{f.document}</pre>
        </Card>
      </div>
    </>
  )
}
