type Series = { key: string; label: string; cls: string }
const monthName = (m: string) => new Date(`${m}-15`).toLocaleDateString('en-US', { month: 'short' })

/** A few months of one or two counts as bars on one scale, each bar labelled with its value. */
export default function MonthBars({ data, series, height = 170 }: { data: Record<string, number | string>[]; series: Series[]; height?: number }) {
  const W = 520, pad = { l: 8, r: 8, t: 18, b: 22 }
  const top = Math.max(1, ...data.flatMap(d => series.map(s => Number(d[s.key]))))
  const slot = (W - pad.l - pad.r) / data.length
  const bw = Math.min(34, (slot - 14) / series.length - 2)
  const y = (v: number) => pad.t + (height - pad.t - pad.b) * (1 - v / top)
  return (
    <div className="chart">
      {series.length > 1 && <div className="net-key">{series.map(s => <span key={s.key}><i className={`k ${s.cls}`} />{s.label}</span>)}</div>}
      <svg viewBox={`0 0 ${W} ${height}`} role="img" aria-label={series.map(s => s.label).join(' and ') + ' by month'}>
        <line className="grid" x1={pad.l} x2={W - pad.r} y1={height - pad.b} y2={height - pad.b} />
        {data.map((d, i) => {
          const x0 = pad.l + i * slot + (slot - series.length * (bw + 2)) / 2
          return <g key={String(d.month)}>
            {series.map((s, j) => {
              const v = Number(d[s.key]), x = x0 + j * (bw + 2), h = height - pad.b - y(v)
              return <g key={s.key}><title>{`${monthName(String(d.month))}: ${v} ${s.label.toLowerCase()}`}</title>
                {v > 0 && <path className={`mb ${s.cls}`} d={`M${x},${height - pad.b} v${-Math.max(0, h - 3)} q0,-3 3,-3 h${bw - 6} q3,0 3,3 v${Math.max(0, h - 3)} z`} />}
                <text className="axis val" x={x + bw / 2} y={y(v) - 4} textAnchor="middle">{v}</text></g>
            })}
            <text className="axis" x={pad.l + i * slot + slot / 2} y={height - 6} textAnchor="middle">{monthName(String(d.month))}</text>
          </g>
        })}
      </svg>
    </div>
  )
}
