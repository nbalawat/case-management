import { fmtMoney } from '../../../api'
import type { Day } from './txns'

/**
 * Money in above the line and money out below it, one bar pair per day. Days with alerted transactions are marked.
 * Click a day to show only that day, shift-click to stretch the range, click the picked day again to clear.
 */
type Props = {
  days: Day[]
  /** Days to keep at full strength; the rest fade. Null means all of them. */
  active: (day: string) => boolean
  onPick: (day: string, extend: boolean) => void
}

export default function DailyHistogram({ days, active, onPick }: Props) {
  if (days.length === 0) return null
  // One scale for both directions; the baseline sits where the larger side needs it, so no half is left empty.
  const W = 720, H = 150, pad = { l: 4, r: 4, t: 8, b: 18 }, plot = H - pad.t - pad.b
  const maxIn = Math.max(0, ...days.map(d => d.in)), maxOut = Math.max(0, ...days.map(d => d.out))
  const top = Math.max(1, maxIn + maxOut), mid = pad.t + plot * (maxIn / top || 0.5)
  const slot = (W - pad.l - pad.r) / days.length, bw = Math.max(1.5, Math.min(18, slot - 2))
  const every = Math.ceil(days.length / 8)
  return (
    <div className="chart txn-hist">
      <div className="net-key"><span><i className="k in" />Money in</span><span><i className="k out" />Money out</span><span><i className="k hit" />Day with alerted activity</span>
        <span className="muted small">Click a day to filter, shift-click to widen</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Money in and out by day">
        <line className="grid" x1={pad.l} x2={W - pad.r} y1={mid} y2={mid} />
        {days.map((d, i) => {
          const x = pad.l + i * slot + (slot - bw) / 2, hi = (d.in / top) * plot, ho = (d.out / top) * plot
          return (
            <g key={d.day} className={`day${d.alerted ? ' hit' : ''}${active(d.day) ? '' : ' dim'}`} onClick={e => onPick(d.day, e.shiftKey)}>
              <title>{`${d.day}: ${fmtMoney(d.in)} in, ${fmtMoney(d.out)} out, ${d.count} transaction${d.count === 1 ? '' : 's'}${d.alerted ? `, ${d.alerted} alerted` : ''}`}</title>
              <rect className="hitbox" x={pad.l + i * slot} y={pad.t} width={slot} height={H - pad.t - pad.b} />
              {d.in > 0 && <rect className="bar in" x={x} y={mid - hi} width={bw} height={hi} rx={1} />}
              {d.out > 0 && <rect className="bar out" x={x} y={mid} width={bw} height={ho} rx={1} />}
              {d.alerted > 0 && <circle className="mark" cx={x + bw / 2} cy={H - pad.b + 5} r={2.5} />}
              {i % every === 0 && <text className="axis" x={pad.l + i * slot} y={H - 1}>{d.day.slice(5)}</text>}
            </g>)
        })}
      </svg>
    </div>
  )
}
