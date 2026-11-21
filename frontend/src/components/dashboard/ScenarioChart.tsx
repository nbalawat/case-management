import { useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import EChart from '../charts/EChart'
import { base, type ChartTheme } from '../charts/theme'
import { casesHref } from './drill'

type Row = { scenario: string; title: string; alerts: number; closed: number; escalated: number }

/**
 * What each scenario's closed cases came to: a filing or confirmed concern, or nothing. The share that led
 * somewhere is written at the end of each bar. Click a bar to open those cases.
 */
export default function ScenarioChart({ rows }: { rows: Row[] }) {
  const nav = useNavigate()
  const shown = useMemo(() => rows.filter(r => r.closed > 0 || r.alerts > 0), [rows])
  const option = useCallback((t: ChartTheme) => {
    const b = base(t)
    const rate = (r: Row) => (r.closed ? `${Math.round((r.escalated / r.closed) * 100)}%` : '–')
    return {
      ...b,
      grid: { ...b.grid, right: 44 },
      tooltip: {
        ...b.tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: t.line, opacity: 0.4 } },
        formatter: (ps: { dataIndex: number }[]) => {
          const r = shown[ps[0].dataIndex]
          return `<b>${r.title}</b><br/>${r.alerts} alerts raised<br/>${r.closed} cases closed, ${r.escalated} led to a filing or confirmed concern (${rate(r)})`
        },
      },
      xAxis: { type: 'value', minInterval: 1, ...b.axis },
      yAxis: { type: 'category', inverse: true, data: shown.map(r => r.title), ...b.axis, axisLine: { show: false }, axisLabel: { ...b.axis.axisLabel, color: t.soft, fontSize: 12.5, width: 210, overflow: 'truncate' } },
      series: [
        { name: 'Filing or confirmed concern', type: 'bar', stack: 'c', data: shown.map(r => r.escalated), barMaxWidth: 16,
          itemStyle: { color: t.series[1], borderColor: t.surface, borderWidth: 1 } },
        { name: 'Closed, no concern', type: 'bar', stack: 'c', data: shown.map(r => r.closed - r.escalated), barMaxWidth: 16,
          itemStyle: { color: t.series[0], borderColor: t.surface, borderWidth: 1, borderRadius: [0, 4, 4, 0] },
          label: { show: true, position: 'right', color: t.ink, fontSize: 12, fontWeight: 600, formatter: (p: { dataIndex: number }) => rate(shown[p.dataIndex]) } },
      ],
    }
  }, [shown])
  const table = <table><thead><tr><th>Scenario</th><th className="num">Alerts</th><th className="num">Closed</th><th className="num">Filing or concern</th></tr></thead>
    <tbody>{shown.map(r => <tr key={r.scenario}><td>{r.title}</td><td className="num">{r.alerts}</td><td className="num">{r.closed}</td><td className="num">{r.escalated}</td></tr>)}</tbody></table>
  return <EChart option={option} height={shown.length * 34 + 50} label="Closed cases by alert scenario and outcome" table={table}
    onPick={e => { const r = shown[e.dataIndex]; if (r) nav(casesHref({ status: 'closed', scenario: r.scenario })) }} />
}
