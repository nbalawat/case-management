import { useCallback } from 'react'
import EChart from '../charts/EChart'
import { base, type ChartTheme } from '../charts/theme'
import type { ModelCard } from './types'

/** Alerts reviewed highest score first against the share of the ones that mattered reached, beside reviewing in
 * arrival order (the diagonal). The gap between the two lines is the work the model saves. */
export default function WorkloadChart({ work }: { work: ModelCard['metrics']['work'] }) {
  const option = useCallback((t: ChartTheme) => {
    const pts = [{ reviewed: 0, found: 0 }, ...work.curve]
    const b = base(t)
    const axis = (name: string, gap: number) => ({ type: 'value', min: 0, max: 1, name, nameLocation: 'middle', nameGap: gap, nameTextStyle: { color: t.muted, fontSize: 11 },
      ...b.axis, axisLabel: { ...b.axis.axisLabel, formatter: (v: number) => `${Math.round(v * 100)}%` } })
    return {
      ...b, grid: { ...b.grid, left: 28, bottom: 24 },
      tooltip: { ...b.tooltip, trigger: 'axis', valueFormatter: (v: number) => `${Math.round(v * 100)}%` },
      xAxis: axis('Alerts reviewed, highest score first', 28),
      yAxis: axis('Alerts that mattered, reached', 44),
      series: [
        { name: 'With the model', type: 'line', data: pts.map(p => [p.reviewed, p.found]), symbolSize: 6, lineStyle: { width: 2.5 },
          markLine: { silent: true, symbol: 'none', lineStyle: { color: t.muted, type: 'dashed' }, label: { color: t.muted, formatter: '95% reached' }, data: [{ yAxis: 0.95 }] } },
        { name: 'In arrival order', type: 'line', data: [[0, 0], [1, 1]], symbol: 'none', lineStyle: { color: t.muted, type: 'dotted', width: 1.5 }, itemStyle: { color: t.muted } },
      ],
    }
  }, [work])
  return (
    <EChart option={option} height={260} label="Share of alerts that mattered reached as alerts are reviewed in score order"
      table={<table><thead><tr><th>Reviewed</th><th>Reached</th></tr></thead>
        <tbody>{work.curve.map(p => <tr key={p.reviewed}><td>{Math.round(p.reviewed * 100)}%</td><td>{Math.round(p.found * 100)}%</td></tr>)}</tbody></table>} />
  )
}
