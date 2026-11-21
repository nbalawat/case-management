import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, ChevronDown, ChevronUp, X } from 'lucide-react'
import { daysUntil, fmtDateTime, fmtMoney, hoursUntil } from '../../api'
import { useApi } from '../../useApi'
import { useNameOf } from '../../shell/people'
import { Dialog, DialogClose, DialogDescription, DialogTitle, SheetContent } from '../kit/dialog'
import { buttonVariants } from '../kit/button'
import { Button } from '../kit/button'
import { Kbd } from '../kit/kbd'
import { Loading, Pill, Priority, Sla, StageClock } from '../ui'
import type { CaseRow } from '../../pages/Cases'

type Step = { title: string; summary: string }
type Detail = {
  case: CaseRow & { narrative?: string }
  party: { id: string; name: string; kind: string; occupation: string; country: string; risk_rating: string; pep?: boolean }
  alerts: { id: string; title: string; explanation: string; score: number }[]
  transactions: { id: string; amount: number; direction: string; channel: string; counterparty: string; ts: string }[]
  agent_run: { recommendation: string; confidence: number; steps: Step[] } | null
  events: { id: number; ts: string; actor: string; actor_kind: string; kind: string }[]
  workflow: { states: { id: string; label: string; kind: string }[] }
}

/**
 * A case at a glance, beside the list: who, what, how urgent, what the agent thinks and what happened last.
 * Up and down move to the neighbouring case in the list without closing the panel.
 */
export default function CaseQuickView({ id, onClose, onStep }: { id: string | null; onClose: () => void; onStep: (delta: number) => void }) {
  const { data, error } = useApi<Detail>(id ? `/cases/${id}` : null)
  const nameOf = useNameOf()
  useEffect(() => {
    if (!id) return
    const key = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown' || e.key === 'j') { e.preventDefault(); onStep(1) }
      if (e.key === 'ArrowUp' || e.key === 'k') { e.preventDefault(); onStep(-1) }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [id, onStep])
  const d = data && data.case.id === id ? data : null
  const c = d?.case
  const run = d?.agent_run
  const alerted = d ? d.transactions.slice(0, 5) : []
  return (
    <Dialog open={!!id} onOpenChange={o => !o && onClose()}>
      <SheetContent aria-describedby="qv-desc">
        <div className="flex items-start gap-3 border-b border-line px-5 py-4">
          <div className="min-w-0 flex-1">
            <DialogTitle className="m-0 truncate text-[19px] font-bold">{c ? c.party_name : 'Loading…'}</DialogTitle>
            <DialogDescription id="qv-desc" className="m-0 text-[13px] text-muted">{c ? `${c.type_title} · ${c.id}` : ' '}</DialogDescription>
          </div>
          <Button size="icon" variant="ghost" aria-label="Previous case" onClick={() => onStep(-1)}><ChevronUp size={17} /></Button>
          <Button size="icon" variant="ghost" aria-label="Next case" onClick={() => onStep(1)}><ChevronDown size={17} /></Button>
          <DialogClose asChild><Button size="icon" variant="ghost" aria-label="Close"><X size={17} /></Button></DialogClose>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {!d ? <Loading error={error} /> : <>
            <div className="qv-strip">
              <Priority p={c!.priority} />
              <span>{c!.state_label}</span>
              {c!.open && <StageClock hours={hoursUntil(c!.stage_due)} />}
              <Sla days={daysUntil(c!.sla_due)} open={c!.open} />
              {c!.restricted && <Pill tone="critical">Restricted</Pill>}
            </div>
            <ol className="qv-stages" aria-label="Workflow">
              {d.workflow.states.filter(s => s.kind !== 'closed').map(s => <li key={s.id} className={s.id === c!.state ? 'now' : ''}>{s.label}</li>)}
            </ol>
            <dl className="facts">
              <dt>Customer</dt><dd><Link to={`/customers/${d.party.id}`}>{d.party.name}</Link> · {d.party.occupation || d.party.kind}, {d.party.country}</dd>
              <dt>Risk rating</dt><dd><Pill tone={d.party.risk_rating === 'high' ? 'serious' : d.party.risk_rating === 'medium' ? 'warning' : 'neutral'}>{d.party.risk_rating}</Pill>{d.party.pep && <Pill tone="serious">PEP</Pill>}</dd>
              <dt>Queue and owner</dt><dd>{c!.queue_label || '–'} · {c!.owner ? nameOf(c!.owner) : 'Unassigned'}</dd>
            </dl>
            {run && (
              <section className={`rec ${run.recommendation === 'escalate' ? 'escalate' : 'close'} mt-4`}>
                <div><span className="muted small">Agent recommendation</span><b>{run.recommendation === 'escalate' ? 'Escalate' : 'Close, no further action'}</b></div>
                <div className="conf"><span className="muted small">Confidence</span><b>{Math.round(run.confidence * 100)}%</b></div>
              </section>)}
            {run && <ul className="qv-list">{run.steps.slice(0, 4).map(s => <li key={s.title}><b>{s.title}.</b> {s.summary}</li>)}</ul>}
            {d.alerts.length > 0 && <>
              <h3>Alerts ({d.alerts.length})</h3>
              <ul className="qv-list">{d.alerts.map(a => <li key={a.id}><b>{a.title}</b> <span className="muted small">score {a.score.toFixed(2)}</span><div className="small">{a.explanation}</div></li>)}</ul>
            </>}
            {alerted.length > 0 && <>
              <h3>Latest transactions</h3>
              <table className="qv-table"><tbody>{alerted.map(t => (
                <tr key={t.id}><td className="nowrap">{fmtDateTime(t.ts)}</td><td>{t.channel}</td><td>{t.counterparty}</td>
                  <td className="num">{t.direction === 'out' ? '−' : ''}{fmtMoney(t.amount)}</td></tr>))}</tbody></table>
            </>}
            <h3>Last activity</h3>
            <ul className="events tight">{d.events.slice(-5).reverse().map(e => (
              <li key={e.id}><Pill tone={e.actor_kind === 'agent' ? 'agent' : e.actor_kind === 'human' ? 'human' : 'neutral'}>{e.actor_kind}</Pill>
                <span><b>{e.kind.replaceAll('.', ' ')}</b> · {nameOf(e.actor)}</span><time>{fmtDateTime(e.ts)}</time></li>))}</ul>
          </>}
        </div>
        <div className="flex items-center gap-3 border-t border-line px-5 py-3">
          {c && <Link to={`/cases/${c.id}`} className={buttonVariants({ variant: 'primary' }) + ' no-underline hover:no-underline'}>Open case <ArrowUpRight size={15} /></Link>}
          <span className="ml-auto text-xs text-muted"><Kbd>↑</Kbd> <Kbd>↓</Kbd> next case · <Kbd>Esc</Kbd> close</span>
        </div>
      </SheetContent>
    </Dialog>
  )
}
