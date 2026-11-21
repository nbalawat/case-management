import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import EChart from '../charts/EChart'
import { base, type ChartTheme } from '../charts/theme'
import { casesHref } from './drill'

type Row = { case_type: string; title: string; state: string; label: string; count: number }

/**
 * Open work in each stage, one small chart per kind of case on a shared scale so lengths compare across kinds.
 * Click a bar to open the cases sitting in that stage.
 */
export default function StageChart({ rows }: { rows: Row[] }) {
  const kinds = [...new Set(rows.map(r => r.case_type))]
  const max = Math.max(1, ...rows.map(r => r.count))
  return <div className="stage-charts">{kinds.map(k => {
    const mine = rows.filter(r => r.case_type === k)
    return <StageBars key={k} title={mine[0].title} rows={mine} max={max} />
  })}</div>
}

function StageBars({ title, rows, max }: { title: string; rows: Row[]; max: number }) {
  const nav = useNavigate()
  const option = useCallback((t: ChartTheme) => {
    const b = base(t)
    return {
      ...b,
      grid: { left: 150, right: 36, top: 4, bottom: 4, containLabel: false }, // one label width, so bars line up across kinds
      tooltip: { ...b.tooltip, trigger: 'item', formatter: (p: { name: string; value: number }) => `${p.name}<br/><b>${p.value}</b> open · click to see them` },
      xAxis: { type: 'value', max, show: false },
      yAxis: { type: 'category', inverse: true, data: rows.map(r => r.label), ...b.axis, axisLine: { show: false }, axisLabel: { ...b.axis.axisLabel, color: t.soft, fontSize: 12.5, width: 140, overflow: 'truncate' } },
      series: [{
        type: 'bar', data: rows.map(r => r.count), barMaxWidth: 14, itemStyle: { color: t.series[0], borderRadius: [0, 4, 4, 0] },
        label: { show: true, position: 'right', color: t.ink, fontSize: 12, fontWeight: 600 },
        emphasis: { itemStyle: { color: t.series[0], opacity: 0.85 } },
      }],
    }
  }, [rows, max])
  return (
    <div className="stage-chart">
      <h3>{title}</h3>
      <EChart option={option} height={rows.length * 28 + 10} label={`Open ${title} cases by stage`}
        onPick={e => { const r = rows[e.dataIndex]; if (r) nav(casesHref({ case_type: r.case_type, state: r.state })) }} />
    </div>
  )
}
