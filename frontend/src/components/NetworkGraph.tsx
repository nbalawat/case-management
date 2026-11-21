import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { fmtMoney } from '../api'

export type NetNode = { id: string; kind: 'customer' | 'counterparty'; label: string; party_id: string | null; flags: string[]; customers: number; open_cases: number; focus: boolean }
export type NetEdge = { source: string; target: string; txns: number; sent: number; received: number; total: number; channels: string[] }
export type Network = { focus: string; nodes: NetNode[]; edges: NetEdge[]; hidden: number }

const W = 920, H = 520, CX = W / 2, CY = H / 2
const flagged = (n: NetNode) => n.flags.some(f => f !== 'common')
export const FLAG_LABEL: Record<string, string> = { watchlist: 'Watchlist name', high_risk_country: 'High-risk country', common: 'Everyday merchant' }

const short = (label: string) => (label.length > 26 ? `${label.slice(0, 25)}…` : label)

type Placed = NetNode & { x: number; y: number; depth: number; angle: number; row: number }

/** Rings around the customer in focus: who they pay and are paid by, then who else deals with those same firms. */
function layout(net: Network, showCommon: boolean): { nodes: Placed[]; edges: NetEdge[] } {
  const keep = new Set(net.nodes.filter(n => n.focus || showCommon || !n.flags.includes('common')).map(n => n.id))
  const edges = net.edges.filter(e => keep.has(e.source) && keep.has(e.target))
  const next = new Map<string, string[]>()
  for (const e of edges) { next.set(e.source, [...(next.get(e.source) ?? []), e.target]); next.set(e.target, [...(next.get(e.target) ?? []), e.source]) }
  const depth = new Map<string, number>([[net.focus, 0]])
  const parent = new Map<string, string>()
  const queue = [net.focus]
  while (queue.length) {
    const at = queue.shift()!
    for (const n of next.get(at) ?? []) if (!depth.has(n)) { depth.set(n, depth.get(at)! + 1); parent.set(n, at); queue.push(n) }
  }
  const byId = new Map(net.nodes.map(n => [n.id, n]))
  const ring = (d: number) => [...depth].filter(([, v]) => v === d).map(([id]) => id)
  const angle = new Map<string, number>([[net.focus, 0]])
  const row = new Map<string, number>() // neighbours on an outer ring alternate between two rows so their names do not collide
  const first = ring(1).sort((a, b) => Number(flagged(byId.get(b)!)) - Number(flagged(byId.get(a)!)) || a.localeCompare(b))
  first.forEach((id, i) => angle.set(id, (2 * Math.PI * i) / Math.max(first.length, 1) - Math.PI / 2))
  // Outer rings sit as close to their parent's direction as an even spacing allows.
  for (let d = 2; d <= 3; d++) {
    const ids = ring(d).sort((a, b) => angle.get(parent.get(a)!)! - angle.get(parent.get(b)!)! || a.localeCompare(b))
    const groups = new Map<string, string[]>()
    ids.forEach(id => groups.set(parent.get(id)!, [...(groups.get(parent.get(id)!) ?? []), id]))
    const step = Math.min((2 * Math.PI) / Math.max(ids.length, 1), 0.26)
    for (const [p, kids] of groups) kids.forEach((id, i) => { angle.set(id, angle.get(p)! + (i - (kids.length - 1) / 2) * step); row.set(id, i % 2) })
  }
  const R = [0, 125, 195, 215]
  const nodes = [...depth].filter(([id]) => byId.has(id)).map(([id, d]) => {
    const a = angle.get(id) ?? 0
    const r = R[Math.min(d, 3)] + (row.get(id) ?? 0) * 30
    return { ...byId.get(id)!, depth: d, angle: a, row: row.get(id) ?? 0, x: CX + r * 1.55 * Math.cos(a), y: CY + r * Math.sin(a) }
  })
  return { nodes, edges: edges.filter(e => depth.has(e.source) && depth.has(e.target)) }
}

export default function NetworkGraph({ network }: { network: Network }) {
  const everyday = network.nodes.filter(n => n.flags.includes('common')).length
  const [showCommon, setShowCommon] = useState(false)
  const [hover, setHover] = useState<string | null>(null)
  const nav = useNavigate()
  const { nodes, edges } = useMemo(() => layout(network, showCommon), [network, showCommon])
  const at = new Map(nodes.map(n => [n.id, n]))
  const most = Math.max(1, ...edges.map(e => e.total))
  const lit = (e: NetEdge) => hover !== null && (e.source === hover || e.target === hover)
  const near = new Set(hover ? [hover, ...edges.filter(lit).flatMap(e => [e.source, e.target])] : [])
  const go = (n: NetNode) => { if (!n.focus) nav(n.party_id ? `/customers/${n.party_id}` : `/counterparties/${n.id}`) }
  const tip = hover ? at.get(hover) : null
  const tipEdges = hover ? edges.filter(lit) : []
  return (
    <div className="net">
      <div className="net-bar">
        <span className="net-key"><i className="k customer" />Customer<i className="k firm" />Counterparty<i className="k flag" />Flagged<i className="k case" />Has an open case</span>
        {everyday > 0 && <label className="check"><input type="checkbox" checked={showCommon} onChange={e => setShowCommon(e.target.checked)} />Show {everyday} everyday merchants</label>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Payment network around this customer">
        {edges.map(e => {
          const a = at.get(e.source)!, b = at.get(e.target)!
          return <line key={`${e.source}-${e.target}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} className={`net-edge${lit(e) ? ' lit' : ''}`}
            strokeWidth={1 + 4 * Math.sqrt(e.total / most)} opacity={hover && !lit(e) ? 0.25 : 1} />
        })}
        {nodes.map(n => {
          const r = n.focus ? 15 : n.kind === 'customer' ? 9 : 8
          const right = Math.cos(n.angle) >= 0
          const dim = hover !== null && !near.has(n.id)
          return (
            <g key={n.id} transform={`translate(${n.x},${n.y})`} className={`net-node ${n.kind}${n.focus ? ' focus' : ''}${flagged(n) ? ' flagged' : ''}`} opacity={dim ? 0.3 : 1}
              tabIndex={n.focus ? -1 : 0} onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(n.id)} onBlur={() => setHover(null)}
              onClick={() => go(n)} onKeyDown={e => e.key === 'Enter' && go(n)}>
              <circle r={r + 8} fill="transparent" />
              {n.kind === 'customer' ? <circle r={r} /> : <rect x={-r} y={-r} width={2 * r} height={2 * r} rx={3} />}
              {n.open_cases > 0 && <circle className="case-dot" cx={r - 1} cy={-r + 1} r={4.5} />}
              {n.focus ? <text y={r + 16} textAnchor="middle" className="focus-label">{n.label}</text>
                : n.depth >= 2 && Math.abs(Math.cos(n.angle)) < 0.6
                  ? <text y={Math.sin(n.angle) > 0 ? r + 14 : -r - 7} textAnchor="middle">{short(n.label)}</text>
                  : <text x={right ? r + 6 : -r - 6} y={4} textAnchor={right ? 'start' : 'end'}>{short(n.label)}</text>}
            </g>)
        })}
      </svg>
      <div className="net-tip" aria-live="polite">
        {tip ? <>
          <b>{tip.label}</b> · {tip.kind === 'customer' ? 'customer' : `counterparty of ${tip.customers} customer${tip.customers === 1 ? '' : 's'}`}
          {tip.flags.map(f => <span key={f} className={`pill ${f === 'common' ? '' : 'serious'}`}>{FLAG_LABEL[f] ?? f}</span>)}
          {tip.open_cases > 0 && <span className="pill warning">{tip.open_cases} open case{tip.open_cases === 1 ? '' : 's'}</span>}
          <span className="muted"> · {tipEdges.length} link{tipEdges.length === 1 ? '' : 's'} shown, {fmtMoney(tipEdges.reduce((t, e) => t + e.total, 0))} across {tipEdges.reduce((t, e) => t + e.txns, 0)} payments</span>
        </> : <span className="muted">Hover or tab to a node for its payments. Click to open it.{network.hidden > 0 ? ` ${network.hidden} smaller links are not drawn.` : ''}</span>}
      </div>
    </div>
  )
}
