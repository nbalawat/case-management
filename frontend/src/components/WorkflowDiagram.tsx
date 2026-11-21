import { useMemo } from 'react'

export type WfState = { id: string; label: string; kind: string }
export type WfStep = { from: string; to: string; label: string; when: string }
type Props = { states: WfState[]; steps: WfStep[]; initial: string; counts?: Record<string, number>; selected?: string | null; onSelect?: (id: string) => void }

const W = 168, H = 46, COL = 236, ROW = 70, PAD = 24, TOP = 30

type Pt = { x: number; y: number; col: number; lane: 'wait' | 'main' | 'end' }

/**
 * Working stages run left to right by distance from the start. A waiting stage sits above the stage that
 * parks into it, and outcomes stack in the last column. Steps that skip a column run in lanes underneath,
 * so no line crosses a box.
 */
function layout(states: WfState[], steps: WfStep[], initial: string) {
  const kind = new Map(states.map(s => [s.id, s.kind]))
  const depth = new Map<string, number>([[initial, 0]])
  const queue = [initial]
  while (queue.length) {
    const cur = queue.shift()!
    for (const s of steps) {
      if (s.from !== cur || depth.has(s.to) || kind.get(s.to) !== 'open') continue
      depth.set(s.to, depth.get(cur)! + 1)
      queue.push(s.to)
    }
  }
  const main = states.filter(s => s.kind === 'open' || s.id === initial)
  for (const s of main) if (!depth.has(s.id)) depth.set(s.id, 1 + Math.max(0, ...steps.filter(t => t.to === s.id).map(t => depth.get(t.from) ?? 0)))
  const waits = states.filter(s => s.kind === 'wait' && s.id !== initial)
  const ends = states.filter(s => s.kind === 'closed')
  const lastCol = Math.max(0, ...main.map(s => depth.get(s.id)!)) + 1
  const perCol = new Map<number, number>()
  const rows = Math.max(1, ...main.map(s => { const c = depth.get(s.id)!; perCol.set(c, (perCol.get(c) ?? 0) + 1); return perCol.get(c)! }), ends.length)
  const mainTop = PAD + TOP + (waits.length ? ROW : 0)
  const pos = new Map<string, Pt>()
  const used = new Map<number, number>()
  for (const s of main) {
    const c = depth.get(s.id)!, n = perCol.get(c)!, i = used.get(c) ?? 0
    used.set(c, i + 1)
    pos.set(s.id, { x: PAD + c * COL, y: mainTop + (i + (rows - n) / 2) * ROW, col: c, lane: 'main' })
  }
  for (const s of waits) {
    const from = steps.find(t => t.to === s.id && pos.has(t.from))
    const c = from ? pos.get(from.from)!.col : 0
    pos.set(s.id, { x: PAD + c * COL + COL / 2, y: PAD + TOP, col: c, lane: 'wait' })
  }
  ends.forEach((s, i) => pos.set(s.id, { x: PAD + lastCol * COL, y: mainTop + (i + (rows - ends.length) / 2) * ROW, col: lastCol, lane: 'end' }))
  const bottom = mainTop + rows * ROW - (ROW - H)
  return { pos, bottom, width: PAD * 2 + lastCol * COL + W }
}

type Route = { d: string; back: boolean }

function route(pos: Map<string, Pt>, steps: WfStep[], bottom: number): { routes: Route[]; height: number } {
  let lanes = 0
  const routes = steps.map(s => {
    const a = pos.get(s.from)!, b = pos.get(s.to)!
    const back = b.col < a.col || (b.col === a.col && a.lane === 'main' && b.lane === 'main' && b.y < a.y)
    if (a.lane === 'wait' || b.lane === 'wait') {  // short hops between a stage and the waiting stage above it
      if (b.lane === 'end') {  // along the waiting row, then down the corridor in front of the outcomes
        const y = a.y + H / 2, vx = b.x - 32, ty = b.y + H / 2, r = 8
        return { back: false, d: `M${a.x + W},${y} L${vx - r},${y} Q${vx},${y} ${vx},${y + r} L${vx},${ty - r} Q${vx},${ty} ${vx + r},${ty} L${b.x - 7},${ty}` }
      }
      if (b.lane === 'wait') return { back: false, d: `M${a.x + W * 0.62},${a.y} C${a.x + W * 0.62},${b.y + H / 2} ${b.x - 30},${b.y + H / 2} ${b.x - 7},${b.y + H / 2}` }
      const tx = b.x + W * (b.col === a.col ? 0.9 : 0.5)
      return { back: false, d: `M${a.x + W * 0.5},${a.y + H} C${a.x + W * 0.5},${a.y + H + 24} ${tx},${b.y - 34} ${tx},${b.y - 7}` }
    }
    if (b.col === a.col + 1) {  // neighbouring columns: a direct curve
      const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x - 7, y2 = b.y + H / 2, mid = (x1 + x2) / 2
      return { back: false, d: `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}` }
    }
    if (b.col === a.col) {
      const down = b.y > a.y, x = a.x + W / 2 + (down ? -18 : 18)
      return { back, d: `M${x},${down ? a.y + H : a.y} L${x},${down ? b.y - 7 : b.y + H + 7}` }
    }
    if (b.col === a.col - 1 && Math.abs(a.y - b.y) < 1) {  // one step back: a shallow arc underneath
      const x1 = a.x + W * 0.3, x2 = b.x + W * 0.7, y = a.y + H
      return { back: true, d: `M${x1},${y} C${x1},${y + 26} ${x2},${y + 26} ${x2},${y + 7}` }
    }
    const y = bottom + 34 + lanes++ * 13, r = 8  // everything else runs in its own lane under the boxes
    const x1 = a.x + W * (back ? 0.2 : 0.8)
    if (b.lane === 'end') {
      const vx = b.x - 18 - (lanes - 1) * 7, ty = b.y + H / 2
      return { back, d: `M${x1},${a.y + H} L${x1},${y - r} Q${x1},${y} ${x1 + r},${y} L${vx - r},${y} Q${vx},${y} ${vx},${y - r} L${vx},${ty + r} Q${vx},${ty} ${vx + r},${ty} L${b.x - 7},${ty}` }
    }
    const x2 = b.x + W * (back ? 0.8 : 0.2), dir = x2 > x1 ? 1 : -1
    return { back, d: `M${x1},${a.y + H} L${x1},${y - r} Q${x1},${y} ${x1 + dir * r},${y} L${x2 - dir * r},${y} Q${x2},${y} ${x2},${y - r} L${x2},${b.y + H + 7}` }
  })
  return { routes, height: bottom + 34 + Math.max(lanes, 1) * 13 + PAD }
}

export default function WorkflowDiagram({ states, steps, initial, counts, selected, onSelect }: Props) {
  const { pos, routes, width, height } = useMemo(() => {
    const l = layout(states, steps, initial)
    return { ...l, ...route(l.pos, steps, l.bottom) }
  }, [states, steps, initial])
  const edge = (s: WfStep, i: number) => {
    const on = selected && (s.from === selected || s.to === selected)
    return <path key={i} d={routes[i].d} className={`wf-edge${s.when ? ' cond' : ''}${on ? ' on' : ''}${routes[i].back ? ' back' : ''}`} markerEnd={`url(#${on ? 'arrow-on' : 'arrow'})`}>
      <title>{s.label}{s.when ? ` (only if ${s.when})` : ''}</title></path>
  }
  return (
    <div className="wf-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} role="img" aria-label="Workflow diagram">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" className="wf-head" /></marker>
          <marker id="arrow-on" viewBox="0 0 10 10" refX="8" refY="5" markerUnits="userSpaceOnUse" markerWidth="10" markerHeight="10" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" className="wf-head on" /></marker>
        </defs>
        {steps.map((s, i) => ({ s, i })).sort((a, b) => Number(!!selected && (a.s.from === selected || a.s.to === selected)) - Number(!!selected && (b.s.from === selected || b.s.to === selected))).map(({ s, i }) => edge(s, i))}
        {states.map(s => {
          const p = pos.get(s.id)!
          const n = counts?.[s.id] ?? 0
          return (
            <g key={s.id} transform={`translate(${p.x},${p.y})`} className={`wf-node ${s.kind}${s.id === selected ? ' sel' : ''}`}
              tabIndex={onSelect ? 0 : undefined} role={onSelect ? 'button' : undefined} aria-label={s.label}
              onClick={() => onSelect?.(s.id)} onKeyDown={e => e.key === 'Enter' && onSelect?.(s.id)}>
              <rect width={W} height={H} rx={s.kind === 'closed' ? 23 : 9} />
              {s.id === initial && <text x={0} y={-8} className="wf-tag">Start</text>}
              <foreignObject width={W} height={H}><div className="wf-label">{s.label}</div></foreignObject>
              {n > 0 && <g transform={`translate(${W - 6},-6)`}><circle r={11} className="wf-badge" /><text textAnchor="middle" dy={4} className="wf-badge-t">{n}</text></g>}
            </g>)
        })}
      </svg>
      <div className="legend wf-legend">
        <span><i className="k open" />Working stage</span><span><i className="k wait" />Waiting on someone outside</span><span><i className="k closed" />Outcome</span>
        <span><i className="k line" />Step</span><span><i className="k dash" />Conditional step</span>{counts && <span><i className="k badge" />Open cases</span>}
      </div>
    </div>
  )
}
