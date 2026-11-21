import { Card, Pill } from '../../ui'
import type { CaseDetail } from './types'

/** The alerts behind the case, each with the score, the scenario version and a link to its transactions. */
export default function AlertsCard({ alerts, onCite }: { alerts: CaseDetail['alerts']; onCite: (ids: string[]) => void }) {
  return (
    <Card title={`Alerts (${alerts.length})`}>
      {alerts.length === 0 && <p className="empty">No alerts. This case was opened by a schedule, an intake channel or another case.</p>}
      {alerts.map(a => (
        <div className="alert" key={a.id}>
          <div><b>{a.title}</b> <Pill>{a.typology}</Pill><span className="score">score {a.score.toFixed(2)}</span></div>
          <p>{a.explanation}</p>
          <div className="muted small">{a.id} · {a.version === 'external' ? 'raised by an outside engine' : `scenario v${a.version}`} · {a.source} · <button className="cite" onClick={() => onCite(a.txn_ids)}>show {a.txn_ids.length} transactions</button></div>
        </div>))}
    </Card>
  )
}
