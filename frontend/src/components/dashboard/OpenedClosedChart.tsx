import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import EChart from '../charts/EChart'
import { base, type ChartTheme } from '../charts/theme'
import { casesHref } from './drill'

type Point = { day: string; opened: number; closed: number }
const short = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })

/** Cases opened and closed per day. Click a bar to open the cases opened, or closed, that day. */
export default function OpenedClosedChart({ data }: { data: Point[] }) {
  const nav = useNavigate()
  const option = useCallback((t: ChartTheme) => {
    const b = base(t)
    const bar = (name: string, key: 'opened' | 'closed', color: string) => ({
      name, type: 'bar', data: data.map(d => d[key]), barGap: '12%', barMaxWidth: 10, itemStyle: { color, borderRadius: [3, 3, 0, 0] },
      emphasis: { focus: 'series' },
    })
    return {
      ...b,
      tooltip: { ...b.tooltip, trigger: 'axis', axisPointer: { type: 'shadow', shadowStyle: { color: t.line, opacity: 0.4 } } },
      xAxis: { type: 'category', data: data.map(d => short(d.day)), ...b.axis, splitLine: { show: false }, axisLabel: { ...b.axis.axisLabel, interval: 6 } },
      yAxis: { type: 'value', minInterval: 1, ...b.axis, axisLine: { show: false } },
      series: [bar('Cases opened', 'opened', t.series[0]), bar('Cases closed', 'closed', t.series[1])],
    }
  }, [data])
  const table = <table><thead><tr><th>Day</th><th className="num">Opened</th><th className="num">Closed</th></tr></thead>
    <tbody>{data.map(d => <tr key={d.day}><td>{short(d.day)}</td><td className="num">{d.opened}</td><td className="num">{d.closed}</td></tr>)}</tbody></table>
  return <EChart option={option} height={240} label="Cases opened and closed per day, last 30 days" table={table}
    onPick={e => {
      const d = data[e.dataIndex]
      if (d) nav(casesHref(e.seriesIndex === 0 ? { status: 'all', opened: d.day } : { status: 'closed', closed: d.day }))
    }} />
}
