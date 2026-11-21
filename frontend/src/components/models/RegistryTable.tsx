import { Pill } from '../ui'
import { day, num, pct, STATUS_TONE, type ModelRow } from './types'

/** Every version of the model, newest first. A row opens its card. */
export default function RegistryTable({ rows, selected, onSelect }: { rows: ModelRow[]; selected: string | null; onSelect: (id: string) => void }) {
  return (
    <table className="ml-registry">
      <thead><tr><th>Version</th><th>Status</th><th>Algorithm</th><th className="num">AUC</th><th className="num">Could wait</th><th>Trained</th><th>Approved</th></tr></thead>
      <tbody>{rows.map(r => (
        <tr key={r.id} className={selected === r.id ? 'selected' : undefined}>
          <td><button className="link" onClick={() => onSelect(r.id)} aria-pressed={selected === r.id}>v{r.version}</button></td>
          <td><Pill tone={STATUS_TONE[r.status]}>{r.status}</Pill></td>
          <td>{r.algorithm.split(' (')[0]}</td>
          <td className="num">{num(r.auc)}</td>
          <td className="num" title="Share of alerts that could wait while still reaching 95% of the ones that mattered">{pct(r.could_wait_at_95)}</td>
          <td className="small">{day(r.trained)} by {r.trained_by}</td>
          <td className="small">{r.approved ? `${day(r.approved)} by ${r.approved_by}` : <span className="muted">not yet</span>}</td>
        </tr>))}
      </tbody>
    </table>
  )
}
