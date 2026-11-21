import { useCallback } from 'react'
import EChart from '../charts/EChart'
import { base, type ChartTheme } from '../charts/theme'

type Series = { key: string; label: string }
const monthName = (m: string) => new Date(`${m}-15T12:00:00`).toLocaleDateString('en-US', { month: 'short', year: '2-digit' })

/** A few months of one or two counts as bars on one scale, each labelled. Click a bar to open what it counts. */
export default function MonthChart({ data, series, label, onPick, height = 220 }: {
  data: Record<string, number | string>[]; series: Series[]; label: string; height?: number
  onPick?: (month: string, key: string) => void
}) {
  const option = useCallback((t: ChartTheme) => {
    const b = base(t)
    return {
      ...b,
      legend: series.length > 1 ? b.legend : { show: false },
      grid: { ...b.grid, top: series.length > 1 ? 36 : 16 },
      tooltip: { ...b.tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: t.line, opacity: 0.4 } } },
      xAxis: { type: 'category', data: data.map(d => monthName(String(d.month))), ...b.axis, splitLine: { show: false } },
      yAxis: { type: 'value', minInterval: 1, ...b.axis, axisLine: { show: false } },
      series: series.map((s, i) => ({
        name: s.label, type: 'bar', data: data.map(d => Number(d[s.key])), barMaxWidth: 28, barGap: '8%',
        itemStyle: { color: t.series[i], borderRadius: [4, 4, 0, 0] },
        label: { show: true, position: 'top', color: t.ink, fontSize: 11.5, formatter: (p: { value: number }) => (p.value ? String(p.value) : '') },
      })),
    }
  }, [data, series])
  const table = <table><thead><tr><th>Month</th>{series.map(s => <th key={s.key} className="num">{s.label}</th>)}</tr></thead>
    <tbody>{data.map(d => <tr key={String(d.month)}><td>{monthName(String(d.month))}</td>{series.map(s => <td key={s.key} className="num">{d[s.key]}</td>)}</tr>)}</tbody></table>
  return <EChart option={option} height={height} label={label} table={table}
    onPick={onPick ? e => { const d = data[e.dataIndex]; if (d) onPick(String(d.month), series[e.seriesIndex ?? 0].key) } : undefined} />
}
