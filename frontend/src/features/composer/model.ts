/**
 * Pure editing operations on a design. The canvas, the palette and the inspector all change a design only through
 * these, so every rule (sensible defaults, who can take a new step, cleaning up after a delete) lives in one place
 * and is unit-tested without rendering anything.
 */
import type { Action, BlockSpec, Catalog, DEdge, Design, DNode, NodeConfig, Preset, StepConfig } from './types'

let counter = 0
export const newId = (prefix: string) => `${prefix}_${Date.now().toString(36)}${(counter++).toString(36)}`

export const blockOf = (cat: Catalog, type: string): BlockSpec | undefined => cat.blocks.find(b => b.id === type)
export const kindOf = (cat: Catalog, node: DNode | undefined) => (node ? blockOf(cat, node.type)?.kind : undefined)
export const nodeById = (d: Design, id: string) => d.nodes.find(n => n.id === id)

/** A label that is not already taken, so new blocks never collide ("Investigation 2"). */
export function freshLabel(d: Design, label: string) {
  const taken = new Set(d.nodes.map(n => (n.config.label ?? '').toLowerCase()))
  if (!taken.has(label.toLowerCase())) return label
  let i = 2
  while (taken.has(`${label} ${i}`.toLowerCase())) i++
  return `${label} ${i}`
}

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x))

/** Add a block from the palette at a canvas position. Only one start is allowed. */
export function addBlock(d: Design, cat: Catalog, type: string, preset: Preset | undefined, position: { x: number; y: number }): { design: Design; id: string | null } {
  const block = blockOf(cat, type)
  if (!block) return { design: d, id: null }
  if (block.kind === 'start' && d.nodes.some(n => n.type === 'start')) return { design: d, id: null }
  const config: NodeConfig = clone({ ...block.defaults, ...(preset?.config ?? {}) })
  if (config.label && block.kind !== 'start') config.label = freshLabel(d, config.label)
  if (block.kind === 'decision') config.branches = (config.branches ?? []).map(b => ({ ...b, id: newId('b') }))
  const id = newId('n')
  return { design: { ...d, nodes: [...d.nodes, { id, type, position, config }] }, id }
}

/** Who should be able to take a step by default: an approval's approvers, else the roles of the people in the stage's queue. */
export function defaultRoles(cat: Catalog, source: DNode): string[] {
  if (source.config.approvers?.length) return [...source.config.approvers]
  const q = cat.reference.queues.find(x => x.id === source.config.queue)
  return q ? [...q.roles] : []
}

/** Whether a step from source to target would go back up the flow: the target already leads to the source, or sits above it. */
export function goesBack(d: Design | undefined, source: DNode, target: DNode): boolean {
  if (d) {
    const seen = new Set([target.id]), queue = [target.id]
    while (queue.length) {
      const cur = queue.shift()!
      for (const e of d.edges) {
        if (e.source !== cur || seen.has(e.target)) continue
        if (e.target === source.id) return true
        seen.add(e.target); queue.push(e.target)
      }
    }
  }
  return target.position.y < source.position.y
}

/** A step name that reads like a button: where it goes, or what it closes as. */
export function defaultStepLabel(cat: Catalog, source: DNode, target: DNode, d?: Design): string {
  const tk = kindOf(cat, target)
  if (source.type === 'approval' && tk !== 'outcome') return goesBack(d, source, target) ? 'Return for rework' : 'Approve'
  if (tk === 'outcome') return `Close as ${(target.config.label ?? 'closed').toLowerCase()}`
  if (tk === 'decision') return 'Decide'
  if (target.type === 'approval') return 'Submit for approval'
  if (target.type === 'wait') return 'Park while waiting'
  return `Send to ${target.config.label ?? 'next stage'}`
}

export function defaultStep(cat: Catalog, source: DNode, target: DNode, d?: Design): StepConfig {
  const sk = kindOf(cat, source)
  if (sk === 'start' || sk === 'decision') return {}
  const label = defaultStepLabel(cat, source, target, d)
  const backwards = label.startsWith('Return')
  const cfg: StepConfig = { label, roles: defaultRoles(cat, source), requires: [] }
  if (source.config.tasks?.length && !backwards) cfg.requires!.push('tasks')
  if (backwards || kindOf(cat, target) === 'outcome') cfg.requires!.push('note')
  if (source.type === 'approval') cfg.four_eyes = !backwards
  return cfg
}

export type ConnectResult = { design: Design; id: string | null; why?: string }

/** Draw a step between two blocks, refusing the connections the platform never allows and saying why. */
export function connect(d: Design, cat: Catalog, source: string, target: string, handle: string | null = null): ConnectResult {
  const s = nodeById(d, source), t = nodeById(d, target)
  if (!s || !t) return { design: d, id: null }
  if (source === target) return { design: d, id: null, why: 'A step cannot lead back into the same block.' }
  const sk = kindOf(cat, s), tk = kindOf(cat, t)
  if (sk === 'outcome') return { design: d, id: null, why: 'An outcome closes the case, so no step can leave it.' }
  if (tk === 'start') return { design: d, id: null, why: 'Nothing can lead back into the start.' }
  if (sk === 'start' && d.edges.some(e => e.source === source)) return { design: d, id: null, why: 'The start already leads somewhere. A case starts in one stage.' }
  if (sk === 'start' && tk !== 'stage') return { design: d, id: null, why: 'The start must lead to a stage people work.' }
  if (sk === 'decision') {
    const branches = s.config.branches ?? []
    const h = handle ?? (s.config.otherwise !== false ? 'otherwise' : branches[0]?.id ?? null)
    if (!h) return { design: d, id: null, why: 'Add a branch to the decision first.' }
    const without = d.edges.filter(e => !(e.source === source && e.handle === h))  // a branch leads to one place
    const id = newId('e')
    return { design: { ...d, edges: [...without, { id, source, target, handle: h, config: {} }] }, id }
  }
  if (d.edges.some(e => e.source === source && e.target === target && !e.handle)) return { design: d, id: null, why: 'These blocks are already joined by a step.' }
  const id = newId('e')
  return { design: { ...d, edges: [...d.edges, { id, source, target, handle: null, config: defaultStep(cat, s, t, d) }] }, id }
}

export function removeNodes(d: Design, ids: string[]): Design {
  const gone = new Set(ids.filter(id => nodeById(d, id)?.type !== 'start' || d.nodes.filter(n => n.type === 'start').length > 1))
  return {
    ...d, nodes: d.nodes.filter(n => !gone.has(n.id)), edges: d.edges.filter(e => !gone.has(e.source) && !gone.has(e.target)),
    settings: gone.has(d.settings.qa_reopen_to ?? '') ? { ...d.settings, qa_reopen_to: null } : d.settings,
  }
}

export const removeEdges = (d: Design, ids: string[]): Design => ({ ...d, edges: d.edges.filter(e => !ids.includes(e.id)) })

export const updateNode = (d: Design, id: string, patch: Partial<NodeConfig>): Design =>
  ({ ...d, nodes: d.nodes.map(n => (n.id === id ? { ...n, config: { ...n.config, ...patch } } : n)) })

export const updateEdge = (d: Design, id: string, patch: Partial<StepConfig>): Design =>
  ({ ...d, edges: d.edges.map(e => (e.id === id ? { ...e, config: { ...e.config, ...patch } } : e)) })

export const moveNodes = (d: Design, moves: Record<string, { x: number; y: number }>): Design =>
  ({ ...d, nodes: d.nodes.map(n => (moves[n.id] ? { ...n, position: moves[n.id] } : n)) })

/** Drop an automatic action on a stage. Returns why not when the block cannot take actions. */
export function attachAction(d: Design, cat: Catalog, nodeId: string, actionId: string): { design: Design; why?: string } {
  const n = nodeById(d, nodeId)
  const spec = cat.actions.find(a => a.id === actionId)
  if (!n || !spec) return { design: d }
  if (!blockOf(cat, n.type)?.accepts_actions) return { design: d, why: `Automatic actions go on stages, not on ${blockOf(cat, n.type)?.label.toLowerCase() ?? 'this block'}s.` }
  const action: Action = { do: actionId }
  for (const f of spec.fields) if (f.default !== null && f.default !== undefined && f.default !== '') action[f.key] = f.default
  if (actionId === 'notify') action.role = cat.reference.roles.find(r => r.id === 'team_lead')?.id ?? cat.reference.roles[0]?.id
  return { design: updateNode(d, nodeId, { actions: [...(n.config.actions ?? []), action] }) }
}

/** Remove the branch from a decision along with the step leaving by it. */
export function removeBranch(d: Design, nodeId: string, branchId: string): Design {
  const n = nodeById(d, nodeId)
  if (!n) return d
  const next = updateNode(d, nodeId, { branches: (n.config.branches ?? []).filter(b => b.id !== branchId) })
  return { ...next, edges: next.edges.filter(e => !(e.source === nodeId && e.handle === branchId)) }
}

export function setOtherwise(d: Design, nodeId: string, on: boolean): Design {
  const next = updateNode(d, nodeId, { otherwise: on })
  return on ? next : { ...next, edges: next.edges.filter(e => !(e.source === nodeId && e.handle === 'otherwise')) }
}

/** Copy blocks (and the steps between them) a little to the right, with fresh ids. */
export function duplicate(d: Design, ids: string[]): { design: Design; ids: string[] } {
  const map = new Map<string, string>()
  const copies: DNode[] = []
  for (const n of d.nodes) {
    if (!ids.includes(n.id) || n.type === 'start') continue
    const id = newId('n')
    map.set(n.id, id)
    const config = clone(n.config)
    delete config.state_id
    if (config.label) config.label = freshLabel({ ...d, nodes: [...d.nodes, ...copies] }, config.label)
    copies.push({ ...n, id, position: { x: n.position.x + 40, y: n.position.y + 60 }, config })
  }
  const edges: DEdge[] = d.edges.filter(e => map.has(e.source) && map.has(e.target))
    .map(e => ({ ...clone(e), id: newId('e'), source: map.get(e.source)!, target: map.get(e.target)! }))
  return { design: { ...d, nodes: [...d.nodes, ...copies], edges: [...d.edges, ...edges] }, ids: copies.map(c => c.id) }
}

/** A short line under a block's name: who works it and how long they have. */
export function nodeSummary(cat: Catalog, n: DNode): string {
  const kind = kindOf(cat, n)
  if (kind === 'start') {
    const sc = n.config.scenarios ?? []
    return sc.length ? `From ${sc.length} alert scenario${sc.length > 1 ? 's' : ''}` : 'Opened by hand'
  }
  if (kind === 'outcome') return n.config.disposition ? n.config.disposition.replaceAll('_', ' ') : 'No disposition'
  if (kind === 'decision') {
    const k = (n.config.branches ?? []).length + (n.config.otherwise !== false ? 1 : 0)
    return `${k} way${k === 1 ? '' : 's'} out, by the facts`
  }
  const q = cat.reference.queues.find(x => x.id === n.config.queue)?.label
  const h = n.config.sla_hours
  const clock = h ? (h >= 48 ? `${Math.round(h / 24)} days` : `${h} h`) : null
  return [q ?? 'No queue', clock].filter(Boolean).join(' · ')
}

/** History for undo and redo: snapshots of the design, newest last. */
export type History = { past: Design[]; present: Design; future: Design[] }
export const LIMIT = 100
export const record = (h: History, next: Design): History =>
  next === h.present ? h : { past: [...h.past, h.present].slice(-LIMIT), present: next, future: [] }
export const undo = (h: History): History =>
  h.past.length ? { past: h.past.slice(0, -1), present: h.past[h.past.length - 1], future: [h.present, ...h.future] } : h
export const redo = (h: History): History =>
  h.future.length ? { past: [...h.past, h.present], present: h.future[0], future: h.future.slice(1) } : h
