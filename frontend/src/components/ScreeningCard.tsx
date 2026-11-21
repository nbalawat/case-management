import { useState } from 'react'
import { api, fmtDate } from '../api'
import { useApi } from '../useApi'
import { useNameOf, usePeople } from '../shell/people'
import { EX_TONE } from './exemptionTone'
import { Card, Pill } from './ui'

export type Exemption = {
  id: string; subject: string; entry_id: string; entry_name: string; entry_ref: string; list: string; case_id: string | null; reason: string; status: string
  proposed_by: string; proposed_at: string; decided_by: string | null; decided_at: string | null; decision_note: string; expires: string | null
}
type View = {
  matches: { subject: string; entry_id: string; entry_name: string; entry_ref: string; score: number; countries: string[]; programs: string[]; aliases: string[] }[]
  exemptions: Exemption[]; can_propose: boolean; can_decide: boolean; me: string
}

/** What this case's hits matched on the list, side by side, and a way to stop the same false match coming back. */
export default function ScreeningCard({ caseId, tick, onChange }: { caseId: string; tick: number; onChange: (m: string) => void }) {
  const { data, reload } = useApi<View>(`/cases/${caseId}/screening`, [tick])
  const nameOf = useNameOf()
  const { holder } = usePeople()
  const [open, setOpen] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [err, setErr] = useState<string | null>(null)
  if (!data || data.matches.length === 0) return null
  const propose = (m: View['matches'][number]) => api.post('/sanctions/exemptions', { subject: m.subject, entry_id: m.entry_id, reason, case_id: caseId })
    .then(() => { setOpen(null); setReason(''); setErr(null); reload(); onChange('Exemption proposed. It waits for a second person.') }).catch(e => setErr(e.message))
  return (
    <Card title="List match">
      {data.matches.map(m => {
        const key = m.subject + m.entry_id
        const made = data.exemptions.find(x => x.entry_id === m.entry_id && x.subject.toLowerCase() === m.subject.toLowerCase() && ['pending', 'active'].includes(x.status))
        return <div key={key} className="match">
          <div className="pair">
            <div><span className="muted small">Screened name</span><b>{m.subject}</b></div>
            <div className="sim" title="Name similarity"><b>{Math.round(m.score * 100)}%</b></div>
            <div><span className="muted small">List entry {m.entry_ref}</span><b>{m.entry_name}</b>
              <span className="small">{[m.countries.join(', '), ...m.programs].filter(Boolean).join(' · ')}{m.aliases.length > 0 && ` · also known as ${m.aliases.join(', ')}`}</span></div>
          </div>
          {made ? <div className="small"><Pill tone={EX_TONE[made.status]}>Exemption {made.status}</Pill> proposed by {nameOf(made.proposed_by)}, {fmtDate(made.proposed_at)}
            {made.status === 'pending' ? `. ${holder('sanctions_decisions')} can decide it on the Sanctions page.` : made.expires ? `, in force until ${made.expires}` : ''}</div>
            : m.score >= 0.999 ? <div className="small muted">The names are identical, so this cannot be exempted on the name alone.</div>
              : data.can_propose && (open === key ? <div className="override-form">
                <textarea rows={2} aria-label="What tells them apart" placeholder="What tells this name apart from the list entry? Audit will read this." value={reason} onChange={e => setReason(e.target.value)} />
                <div className="inline"><button className="primary" disabled={reason.trim().length < 15} onClick={() => propose(m)}>Propose exemption</button><button onClick={() => setOpen(null)}>Cancel</button>
                  <span className="muted small">A second person must agree before it takes effect.</span></div>
                {err && <p className="msg" role="alert">{err}</p>}
              </div> : <button className="ghost" onClick={() => { setOpen(key); setReason('') }}>Not the same party: stop this match recurring…</button>)}
        </div>
      })}
    </Card>
  )
}
