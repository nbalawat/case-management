import { useEffect, useRef, useState, type ReactNode } from 'react'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { AriaComponent, DataZoomInsideComponent, GridComponent, LegendComponent, MarkLineComponent, TooltipComponent } from 'echarts/components'
import { SVGRenderer } from 'echarts/renderers'
import type { EChartsCoreOption, ECElementEvent } from 'echarts/core'
import { readTheme, type ChartTheme } from './theme'

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, MarkLineComponent, DataZoomInsideComponent, AriaComponent, SVGRenderer])

type Props = {
  /** Built from the current theme, so colours follow light and dark. */
  option: (t: ChartTheme) => EChartsCoreOption
  height?: number
  label: string
  /** A click on a mark: dashboards use it to open the cases behind the number. */
  onPick?: (e: ECElementEvent) => void
  /** The same figures as a table, for screen readers and anyone who prefers numbers. */
  table?: ReactNode
}

/** Watches the page theme, so charts repaint when someone switches light or dark. */
function useThemeVersion() {
  const [v, setV] = useState(0)
  useEffect(() => {
    const mo = new MutationObserver(() => setV(n => n + 1))
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    const mq = window.matchMedia?.('(prefers-color-scheme: dark)')
    const on = () => setV(n => n + 1)
    mq?.addEventListener?.('change', on)
    return () => { mo.disconnect(); mq?.removeEventListener?.('change', on) }
  }, [])
  return v
}

/** One ECharts instance in a div, sized to its container, themed from CSS tokens, with an optional table view. */
export default function EChart({ option, height = 240, label, onPick, table }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const chart = useRef<echarts.ECharts | null>(null)
  const pick = useRef(onPick)
  const themeVersion = useThemeVersion()
  useEffect(() => { pick.current = onPick }, [onPick])

  useEffect(() => {
    if (!el.current) return
    const c = echarts.init(el.current, undefined, { renderer: 'svg' })
    chart.current = c
    c.on('click', e => pick.current?.(e as ECElementEvent))
    const ro = new ResizeObserver(() => c.resize())
    ro.observe(el.current)
    return () => { ro.disconnect(); c.dispose(); chart.current = null }
  }, [])

  useEffect(() => {
    chart.current?.setOption({ aria: { enabled: true, label: { description: label } }, ...option(readTheme()) }, true)
  }, [option, label, themeVersion])

  return (
    <div className="echart">
      <div ref={el} style={{ height }} role="img" aria-label={label} className={onPick ? 'pickable' : undefined} />
      {table && <details className="chart-table"><summary>The same figures as a table</summary>{table}</details>}
    </div>
  )
}
