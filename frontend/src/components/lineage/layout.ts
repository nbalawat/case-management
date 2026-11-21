/** Positions for the lineage graph: one column per layer (the server's longest path from a source), nodes stacked
 * in each column in an order that keeps a node near what feeds it, so lines cross as little as a simple pass can. */
export type LNode = { id: string; type: 'dataset' | 'job'; name: string; layer: number; namespace?: string; kind?: string; rows_written?: number;
  runs?: number; failed?: number; last_run?: string | null; last_state?: string | null }
export type LEdge = { from: string; to: string; rows: number | null; runs: number }
export type Placed = LNode & { x: number; y: number; w: number; h: number }

export const NODE = { w: 190, h: 50, col: 250, row: 72, pad: 20 }

export function layout(nodes: LNode[], edges: LEdge[]) {
  const cols = new Map<number, LNode[]>()
  for (const n of nodes) cols.set(n.layer, [...(cols.get(n.layer) ?? []), n])
  const order = new Map<string, number>()
  const layers = [...cols.keys()].sort((a, b) => a - b)
  for (const l of layers) {
    const parentsOf = (id: string) => edges.filter(e => e.to === id).map(e => order.get(e.from)).filter((v): v is number => v !== undefined)
    const col = cols.get(l)!
    const score = new Map(col.map(n => { const p = parentsOf(n.id); return [n.id, p.length ? p.reduce((a, b) => a + b, 0) / p.length : Infinity] }))
    col.sort((a, b) => (score.get(a.id)! - score.get(b.id)!) || a.name.localeCompare(b.name))
    col.forEach((n, i) => order.set(n.id, i))
  }
  const tallest = Math.max(1, ...[...cols.values()].map(c => c.length))
  const placed = new Map<string, Placed>()
  layers.forEach((l, ci) => {
    const col = cols.get(l)!
    const offset = (tallest - col.length) * NODE.row / 2
    col.forEach((n, i) => placed.set(n.id, { ...n, x: NODE.pad + ci * NODE.col, y: NODE.pad + offset + i * NODE.row, w: NODE.w, h: NODE.h }))
  })
  return { placed, width: NODE.pad * 2 + Math.max(0, layers.length - 1) * NODE.col + NODE.w, height: NODE.pad * 2 + (tallest - 1) * NODE.row + NODE.h }
}

export function curve(a: Placed, b: Placed) {
  const x1 = a.x + a.w, y1 = a.y + a.h / 2, x2 = b.x - 6, y2 = b.y + b.h / 2
  if (x2 <= x1) {  // a job that writes back what it read: a loop under the pair
    const y = Math.max(a.y + a.h, b.y + b.h) + 18
    return `M${a.x + a.w / 2},${a.y + a.h} C${a.x + a.w / 2},${y} ${b.x + b.w / 2},${y} ${b.x + b.w / 2},${b.y + b.h + 6}`
  }
  const mid = (x1 + x2) / 2
  return `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`
}

/** Everything upstream and downstream of a node, for highlighting a path. */
export function connected(id: string, edges: LEdge[]) {
  const seen = new Set([id])
  for (const dir of ['up', 'down'] as const) {
    const todo = [id]
    while (todo.length) {
      const cur = todo.pop()!
      for (const e of edges) {
        const next = dir === 'up' ? (e.to === cur ? e.from : null) : (e.from === cur ? e.to : null)
        if (next && !seen.has(next)) { seen.add(next); todo.push(next) }
      }
    }
  }
  return seen
}
