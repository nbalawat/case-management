/** Chart colours come from the same CSS tokens as the rest of the page, so charts follow light and dark themes. */
export type ChartTheme = {
  series: string[]; ink: string; soft: string; muted: string; line: string; surface: string; crit: string; font: string
}

const token = (style: CSSStyleDeclaration, name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback

export function readTheme(el: Element = document.documentElement): ChartTheme {
  const s = getComputedStyle(el)
  return {
    series: ['--series-1', '--series-2', '--series-3', '--series-4'].map((n, i) => token(s, n, ['#2a78d6', '#eb6834', '#1baf7a', '#eda100'][i])),
    ink: token(s, '--ink', '#0b0f1a'), soft: token(s, '--soft', '#52586a'), muted: token(s, '--muted', '#7b8194'),
    line: token(s, '--line', '#e3e6ee'), surface: token(s, '--surface', '#fff'), crit: token(s, '--crit', '#b3261e'),
    font: 'Inter, system-ui, sans-serif',
  }
}

/** Recessive axes and grid, text in ink tokens, a tooltip on the page surface: the shared base of every chart. */
export function base(t: ChartTheme) {
  const axis = {
    axisLine: { lineStyle: { color: t.line } }, axisTick: { show: false },
    axisLabel: { color: t.muted, fontSize: 11, fontFamily: t.font }, splitLine: { lineStyle: { color: t.line } },
  }
  return {
    color: t.series,
    textStyle: { fontFamily: t.font, color: t.soft },
    animationDuration: 300,
    grid: { left: 8, right: 16, top: 36, bottom: 8, containLabel: true },
    legend: { top: 0, left: 0, icon: 'roundRect', itemWidth: 11, itemHeight: 11, textStyle: { color: t.soft, fontSize: 12.5 } },
    tooltip: {
      backgroundColor: t.surface, borderColor: t.line, borderWidth: 1, padding: [8, 10],
      textStyle: { color: t.ink, fontSize: 12.5, fontFamily: t.font }, extraCssText: 'box-shadow: 0 6px 18px rgba(0,0,0,.12); border-radius: 8px;',
    },
    axis,
  }
}
