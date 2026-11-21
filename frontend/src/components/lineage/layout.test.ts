import { describe, expect, it } from 'vitest'
import { connected, curve, layout, NODE, type LEdge, type LNode } from './layout'

const ds = (id: string, layer: number): LNode => ({ id, type: 'dataset', name: id, layer })
const job = (id: string, layer: number): LNode => ({ id, type: 'job', name: id, layer })

describe('lineage layout', () => {
  const nodes = [ds('files', 0), ds('topic', 0), job('pipeline', 1), job('stream', 1), ds('txn', 2), job('lake', 3), ds('iceberg', 4)]
  const edges: LEdge[] = [
    { from: 'files', to: 'pipeline', rows: null, runs: 1 }, { from: 'topic', to: 'stream', rows: null, runs: 1 },
    { from: 'pipeline', to: 'txn', rows: 5, runs: 1 }, { from: 'stream', to: 'txn', rows: 2, runs: 1 },
    { from: 'txn', to: 'lake', rows: null, runs: 1 }, { from: 'lake', to: 'iceberg', rows: 7, runs: 1 },
  ]

  it('puts each layer in its own column, left to right', () => {
    const { placed, width } = layout(nodes, edges)
    expect(placed.get('files')!.x).toBe(NODE.pad)
    expect(placed.get('iceberg')!.x).toBe(NODE.pad + 4 * NODE.col)
    expect(width).toBe(NODE.pad * 2 + 4 * NODE.col + NODE.w)
  })

  it('keeps a node in line with what feeds it', () => {
    const { placed } = layout(nodes, edges)
    expect(placed.get('files')!.y).toBeLessThan(placed.get('topic')!.y)
    expect(placed.get('pipeline')!.y).toBeLessThan(placed.get('stream')!.y)
  })

  it('lights up everything upstream and downstream, and nothing beside', () => {
    expect([...connected('pipeline', edges)].sort()).toEqual(['files', 'iceberg', 'lake', 'pipeline', 'txn'])
  })

  it('draws a loop under a job that writes what it read', () => {
    const { placed } = layout([ds('a', 0), job('j', 0)], [])
    expect(curve(placed.get('a')!, placed.get('j')!)).toMatch(/^M[\d.]+,[\d.]+ C/)
  })
})
