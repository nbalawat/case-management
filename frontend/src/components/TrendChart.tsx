import { useState } from 'react'

type Point = { day: string; opened: number; closed: number }
const SERIES = [
  { key: 'opened' as const, label: 'Cases opened', color: 'var(--series-1)' },
  { key: 'closed' as const, label: 'Cases closed', color: 'var(--series-2)' },
]

/** Two-series line chart with a crosshair tooltip. One y-axis; legend plus end labels. */
export default function TrendChart({ data }: { data: Point[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const W = 640, H = 220, L = 28, R = 12, T = 10, B = 24
  const max = Math.max(2, ...data.flatMap(d => [d.opened, d.closed]))
  const x = (i: number) => L + (i / (data.length - 1)) * (W - L - R)
  const y = (v: number) => T + (1 - v / max) * (H - T - B)
  const ticks = [0, Math.ceil(max / 2), max]
  const label = (iso: string) => new Date(iso + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  return (
    <div className="chart">
      <div className="legend">{SERIES.map(s => <span key={s.key}><i style={{ background: s.color }} />{s.label}</span>)}</div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Cases opened and closed per day, last 30 days"
        onMouseLeave={() => setHover(null)}
        onMouseMove={e => {
          const r = e.currentTarget.getBoundingClientRect()
          const px = ((e.clientX - r.left) / r.width) * W
          setHover(Math.max(0, Math.min(data.length - 1, Math.round(((px - L) / (W - L - R)) * (data.length - 1)))))
        }}>
        {ticks.map(t => <g key={t}><line x1={L} x2={W - R} y1={y(t)} y2={y(t)} className="grid" /><text x={L - 6} y={y(t) + 4} textAnchor="end" className="axis">{t}</text></g>)}
        {[0, 10, 20, data.length - 1].map(i => <text key={i} x={x(i)} y={H - 6} textAnchor={i === 0 ? 'start' : i === data.length - 1 ? 'end' : 'middle'} className="axis">{label(data[i].day)}</text>)}
        {SERIES.map(s => <polyline key={s.key} fill="none" stroke={s.color} strokeWidth="2" strokeLinejoin="round"
          points={data.map((d, i) => `${x(i)},${y(d[s.key])}`).join(' ')} />)}
        {hover !== null && <>
          <line x1={x(hover)} x2={x(hover)} y1={T} y2={H - B} className="cross" />
          {SERIES.map(s => <circle key={s.key} cx={x(hover)} cy={y(data[hover][s.key])} r="4" fill={s.color} stroke="var(--surface)" strokeWidth="2" />)}
        </>}
      </svg>
      {hover !== null && <div className="tip" style={{ left: `${(x(hover) / W) * 100}%` }}>
        <b>{label(data[hover].day)}</b>
        {SERIES.map(s => <div key={s.key}><i style={{ background: s.color }} />{s.label}: {data[hover][s.key]}</div>)}
      </div>}
    </div>
  )
}
