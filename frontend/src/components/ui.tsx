import { Link } from 'react-router-dom'
import type { ReactNode } from 'react'

export const Pill = ({ tone = 'neutral', children }: { tone?: string; children: ReactNode }) =>
  <span className={`pill ${tone}`}>{children}</span>

const PRI: Record<string, string> = { critical: 'critical', high: 'serious', medium: 'warning', low: 'neutral' }
export const Priority = ({ p }: { p: string }) => <Pill tone={PRI[p]}>{p}</Pill>

export const Rec = ({ r }: { r: string | null }) =>
  r ? <Pill tone={r === 'escalate' ? 'serious' : 'good'}>Agent: {r === 'escalate' ? 'escalate' : 'close'}</Pill> : <span className="muted">–</span>

export function Sla({ days, open }: { days: number | null; open: boolean }) {
  if (!open || days === null) return <span className="muted">Closed</span>
  if (days < 0) return <Pill tone="critical">{-days}d overdue</Pill>
  if (days <= 7) return <Pill tone="warning">{days}d left</Pill>
  return <span>{days}d left</span>
}

export const Card = ({ title, action, children, className = '' }: { title?: string; action?: ReactNode; children: ReactNode; className?: string }) => (
  <section className={`card ${className}`}>
    {title && <div className="card-h"><h2>{title}</h2>{action}</div>}
    {children}
  </section>
)

export const Loading = ({ error }: { error?: string | null }) =>
  <div className="loading">{error ? `Could not load: ${error}` : 'Loading…'}</div>

const span = (h: number) => (Math.abs(h) >= 48 ? `${Math.round(Math.abs(h) / 24)}d` : `${Math.abs(h)}h`)

/** The clock on the current stage, as opposed to the regulatory deadline on the whole case. */
export function StageClock({ hours, kind }: { hours: number | null; kind?: string }) {
  if (hours === null) return <span className="muted">–</span>
  if (hours < 0) return <Pill tone="critical">{span(hours)} over</Pill>
  if (hours <= 8) return <Pill tone="warning">{span(hours)} left</Pill>
  return <span>{span(hours)} left{kind === 'wait' ? ' (waiting)' : ''}</span>
}

/** A headline figure. With `to`, the whole tile opens the records behind the number. */
export const Kpi = ({ label, value, sub, bad, to }: { label: string; value: ReactNode; sub?: ReactNode; bad?: boolean; to?: string }) => {
  const body = <><span>{label}</span><b className={bad ? 'bad' : ''}>{value}</b>{sub && <small>{sub}</small>}</>
  return to ? <Link className="kpi kpi-link" to={to} title="Open the records behind this figure">{body}</Link> : <div className="kpi">{body}</div>
}
