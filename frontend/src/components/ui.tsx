import type { ReactNode } from 'react'

export const Pill = ({ tone = 'neutral', children }: { tone?: string; children: ReactNode }) =>
  <span className={`pill ${tone}`}>{children}</span>

const PRI: Record<string, string> = { critical: 'critical', high: 'serious', medium: 'warning', low: 'neutral' }
export const Priority = ({ p }: { p: string }) => <Pill tone={PRI[p]}>{p}</Pill>

export const Rec = ({ r }: { r: string | null }) =>
  r ? <Pill tone={r === 'escalate' ? 'serious' : 'good'}>Agent: {r === 'escalate' ? 'escalate' : 'close'}</Pill> : <span className="muted">No agent run</span>

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
