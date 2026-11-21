import { useCallback, useMemo, useState } from 'react'
import {
  Background, BackgroundVariant, Controls, MarkerType, MiniMap, ReactFlow, useReactFlow,
  type Connection, type FinalConnectionState, type NodeChange, type OnSelectionChangeParams,
} from '@xyflow/react'
import BlockNode, { type BlockFlowNode } from './nodes/BlockNode'
import StepEdge, { type StepFlowEdge } from './nodes/StepEdge'
import { ACTION_MIME, BLOCK_MIME } from './panels/Palette'
import { addBlock, attachAction, blockOf, connect, kindOf, moveNodes, nodeSummary, removeEdges, removeNodes } from './model'
import type { Selection } from './panels/Inspector'
import type { Catalog, Design, Issue } from './types'

const nodeTypes = { block: BlockNode }
const edgeTypes = { step: StepEdge }
const MINIMAP: Record<string, string> = { start: '#0a7f6f', stage: '#1240e0', wait: '#b26a00', approval: '#7a3fc0', decision: '#c2410c', outcome: '#475569' }

type Props = {
  cat: Catalog; design: Design; issues: Issue[]; selection: Selection; readOnly: boolean
  trial: { current: string | null; visited: string[] }
  onChange: (d: Design) => void; onSelect: (s: Selection) => void; onNotice: (msg: string) => void
}

/** The canvas: blocks drawn from the design, steps between them, and every gesture turned into a change to the design. */
export default function Canvas({ cat, design, issues, selection, readOnly, trial, onChange, onSelect, onNotice }: Props) {
  const flow = useReactFlow()
  const [dragging, setDragging] = useState<Record<string, { x: number; y: number }>>({})
  const [dropOn, setDropOn] = useState<string | null>(null)
  // The canvas is controlled, so measured sizes must be handed back on every render or React Flow hides the blocks.
  const [sizes, setSizes] = useState<Record<string, { width: number; height: number }>>({})

  const byTarget = useMemo(() => {
    const m = new Map<string, Issue[]>()
    for (const i of issues) if (i.id) m.set(`${i.target}:${i.id}`, [...(m.get(`${i.target}:${i.id}`) ?? []), i])
    return m
  }, [issues])

  const nodes: BlockFlowNode[] = useMemo(() => design.nodes.map(n => ({
    id: n.id, type: 'block', position: dragging[n.id] ?? n.position, measured: sizes[n.id], selected: selection?.kind === 'node' && selection.id === n.id,
    draggable: !readOnly, connectable: !readOnly, deletable: !readOnly && n.type !== 'start',
    data: { node: n, block: blockOf(cat, n.type), summary: nodeSummary(cat, n), issues: byTarget.get(`node:${n.id}`) ?? [], actions: cat.actions,
            trial: trial.current === n.id ? 'current' : trial.visited.includes(n.id) ? 'visited' : null, dropTarget: dropOn === n.id },
  })), [design.nodes, dragging, sizes, selection, readOnly, cat, byTarget, trial, dropOn])

  const edges: StepFlowEdge[] = useMemo(() => {
    const arriving = new Map<string, number>()
    return design.edges.map(e => {
    const src = design.nodes.find(n => n.id === e.source)
    const route = kindOf(cat, src) !== 'stage'
    const roles = e.config.roles?.length ? e.config.roles : src?.config.approvers ?? []
    const who = roles.map(r => cat.reference.roles.find(x => x.id === r)?.label ?? r).join(', ')
    const labelled = kindOf(cat, src) === 'stage'
    const above = (design.nodes.find(n => n.id === e.target)?.position.y ?? 0) < (src?.position.y ?? 0)
    const stack = labelled && !above ? arriving.get(e.target) ?? 0 : 0
    if (labelled && !above) arriving.set(e.target, stack + 1)
    const trialEdge = trial.visited.includes(e.target) && (trial.visited.includes(e.source) || src?.type === 'start' || src?.type === 'decision')
    return {
      id: e.id, source: e.source, target: e.target, sourceHandle: e.handle ?? undefined, type: 'step',
      selected: selection?.kind === 'edge' && selection.id === e.id, deletable: !readOnly,
      markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
      data: { edge: e, route, issues: byTarget.get(`edge:${e.id}`) ?? [], trial: trialEdge, who, stack },
    }
  })
  }, [design.edges, design.nodes, cat, selection, readOnly, byTarget, trial])

  const onNodesChange = useCallback((changes: NodeChange<BlockFlowNode>[]) => {
    const moves: Record<string, { x: number; y: number }> = {}
    const measured: Record<string, { width: number; height: number }> = {}
    let done = false
    for (const c of changes) {
      if (c.type === 'dimensions' && c.dimensions) measured[c.id] = c.dimensions
      if (c.type === 'position' && c.position) moves[c.id] = c.position
      if (c.type === 'position' && c.dragging === false) done = true
    }
    if (Object.keys(measured).length) setSizes(z => ({ ...z, ...measured }))
    if (Object.keys(moves).length) setDragging(d => ({ ...d, ...moves }))
    if (done) {
      setDragging(d => {
        if (Object.keys(d).length) onChange(moveNodes(design, d))
        return {}
      })
    }
  }, [design, onChange])

  const onEdgesChange = useCallback(() => {}, [])

  const onConnect = useCallback((c: Connection) => {
    if (readOnly || !c.source || !c.target) return
    const r = connect(design, cat, c.source, c.target, c.sourceHandle ?? null)
    if (r.why) onNotice(r.why)
    if (r.id) { onChange(r.design); onSelect(kindOf(cat, design.nodes.find(n => n.id === c.source)) === 'stage' ? { kind: 'edge', id: r.id } : null) }
  }, [design, cat, readOnly, onChange, onSelect, onNotice])

  /** Letting go anywhere on a block joins it, not only on its small connection dot. */
  const onConnectEnd = useCallback((event: MouseEvent | TouchEvent, state: FinalConnectionState) => {
    if (state.isValid || !state.fromNode || state.fromHandle?.type !== 'source') return
    const point = 'changedTouches' in event ? event.changedTouches[0] : event
    const id = document.elementFromPoint(point.clientX, point.clientY)?.closest('.react-flow__node')?.getAttribute('data-id')
    if (id && id !== state.fromNode.id) onConnect({ source: state.fromNode.id, target: id, sourceHandle: state.fromHandle.id ?? null, targetHandle: null })
  }, [onConnect])

  const nodeUnder = (target: EventTarget | null) => (target as HTMLElement | null)?.closest?.('.react-flow__node')?.getAttribute('data-id') ?? null

  const onDragOver = (e: React.DragEvent) => {
    if (readOnly) return
    const types = Array.from(e.dataTransfer.types)
    if (!types.includes(BLOCK_MIME) && !types.includes(ACTION_MIME)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'copy'
    setDropOn(types.includes(ACTION_MIME) ? nodeUnder(e.target) : null)
  }

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDropOn(null)
    if (readOnly) return
    const action = e.dataTransfer.getData(ACTION_MIME)
    if (action) {
      const id = nodeUnder(e.target)
      if (!id) { onNotice('Drop an automatic action onto a stage.'); return }
      const r = attachAction(design, cat, id, action)
      if (r.why) onNotice(r.why)
      else { onChange(r.design); onSelect({ kind: 'node', id }) }
      return
    }
    const raw = e.dataTransfer.getData(BLOCK_MIME)
    if (!raw) return
    const { type, preset } = JSON.parse(raw) as { type: string; preset: string }
    const block = blockOf(cat, type)
    const pos = flow.screenToFlowPosition({ x: e.clientX - 110, y: e.clientY - 30 })
    const r = addBlock(design, cat, type, block?.presets.find(p => p.id === preset), pos)
    if (r.id) { onChange(r.design); onSelect({ kind: 'node', id: r.id }) }
    else if (block?.kind === 'start') onNotice('This workflow already has its start.')
  }

  const onSelectionChange = useCallback(({ nodes: ns, edges: es }: OnSelectionChangeParams) => {
    if (ns.length === 1 && !es.length) onSelect({ kind: 'node', id: ns[0].id })
    else if (es.length === 1 && !ns.length) onSelect({ kind: 'edge', id: es[0].id })
  }, [onSelect])

  return (
    <div className="cf-canvas" onDragOver={onDragOver} onDrop={onDrop} onDragLeave={() => setDropOn(null)}>
      <ReactFlow<BlockFlowNode, StepFlowEdge>
        nodes={nodes} edges={edges} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        onNodesChange={onNodesChange} onEdgesChange={onEdgesChange} onConnect={onConnect} onConnectEnd={onConnectEnd}
        onNodeClick={(_, n) => onSelect({ kind: 'node', id: n.id })} onEdgeClick={(_, e) => onSelect({ kind: 'edge', id: e.id })}
        onPaneClick={() => onSelect(null)} onSelectionChange={onSelectionChange}
        onNodesDelete={ns => onChange(removeNodes(design, ns.map(n => n.id)))} onEdgesDelete={es => onChange(removeEdges(design, es.map(e => e.id)))}
        deleteKeyCode={readOnly ? null : ['Delete', 'Backspace']} nodesDraggable={!readOnly} nodesConnectable={!readOnly}
        fitView fitViewOptions={{ padding: 0.18, maxZoom: 1 }} minZoom={0.2} maxZoom={1.8} snapToGrid snapGrid={[10, 10]}
        connectionRadius={36} proOptions={{ hideAttribution: true }} defaultEdgeOptions={{ type: 'step' }}>
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} />
        <Controls showInteractive={false} position="bottom-left" />
        <MiniMap pannable zoomable position="bottom-right" style={{ width: 150, height: 110 }} nodeBorderRadius={6}
          nodeColor={n => MINIMAP[(n as BlockFlowNode).data.node.type] ?? '#64748b'} maskColor="rgb(120 130 160 / 0.18)" />
      </ReactFlow>
    </div>
  )
}
