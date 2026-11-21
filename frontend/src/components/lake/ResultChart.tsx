import { useCallback } from 'react'
import EChart from '../charts/EChart'
import { base, type ChartTheme } from '../charts/theme'

import { chartable, type Result } from './chartable'

/** A query result as bars (few rows) or a line (many, or time on the axis). The table view is the result grid itself. */
export default function ResultChart({ result }: { result: Result }) {
  const spec = chartable(result)
  const option = useCallback((t: ChartTheme) => {
    if (!spec) return {}
    const b = base(t)
    const time = /DATE|TIMESTAMP/.test(result.types[spec.label])
    const rows = time ? [...result.rows].sort((a, c) => String(a[spec.label]).localeCompare(String(c[spec.label]))) : result.rows
    const line = time || rows.length > 40
    // Columns of very different size (a count beside a sum of money) get their own scale on the right.
    const peak = (i: number) => Math.max(1, ...rows.map(r => Math.abs(Number(r[i])) || 0))
    const split = spec.values.length > 1 && spec.values.slice(1).some(i => peak(spec.values[0]) / peak(i) > 20 || peak(i) / peak(spec.values[0]) > 20)
    return {
      ...b,
      legend: spec.values.length > 1 ? b.legend : { show: false },
      grid: { ...b.grid, top: spec.values.length > 1 ? 36 : 16 },
      tooltip: { ...b.tooltip, trigger: 'axis' },
      xAxis: { type: 'category', data: rows.map(r => String(r[spec.label]).slice(0, time ? 10 : 40)), ...b.axis, splitLine: { show: false } },
      yAxis: [{ type: 'value', ...b.axis, axisLine: { show: false } }, ...(split ? [{ type: 'value', ...b.axis, axisLine: { show: false }, splitLine: { show: false } }] : [])],
      series: spec.values.map((i, n) => ({
        name: result.columns[i], type: line ? 'line' : 'bar', data: rows.map(r => Number(r[i])), showSymbol: false, barMaxWidth: 26,
        yAxisIndex: split && n > 0 ? 1 : 0,
        itemStyle: { color: t.series[n], borderRadius: line ? 0 : [4, 4, 0, 0] }, lineStyle: { width: 2 },
      })),
    }
  }, [result, spec])
  if (!spec) return null
  return <EChart option={option} height={260} label={`Chart of ${spec.values.map(i => result.columns[i]).join(', ')} by ${result.columns[spec.label]}`} />
}
