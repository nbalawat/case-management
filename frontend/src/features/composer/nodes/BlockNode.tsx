import { memo } from 'react'
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react'
import { AlertTriangle, CircleAlert, ListChecks, Lock } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { CatalogIcon } from '../CatalogIcon'
import { toneOf } from '../icons'
import type { ActionSpec, BlockSpec, DNode, Issue } from '../types'

export type BlockData = {
  node: DNode
  block: BlockSpec | undefined
  summary: string
  issues: Issue[]
  actions: ActionSpec[]
  trial?: 'current' | 'visited' | null
  dropTarget?: boolean
}
export type BlockFlowNode = Node<BlockData, 'block'>

/** One block on the canvas. Steps leave from the bottom and arrive at the top; a decision has one exit per branch. */
function BlockNode({ data, selected }: NodeProps<BlockFlowNode>) {
  const { node, block, summary, issues, actions, trial, dropTarget } = data
  const kind = block?.kind ?? 'stage'
  const tone = toneOf(node.type, kind)
  const icon = block?.presets.find(p => p.config.label === node.config.label)?.icon ?? block?.icon
  const errors = issues.filter(i => i.level === 'error').length
  const warnings = issues.length - errors
  const tasks = node.config.tasks?.length ?? 0
  const four = node.type === 'approval' && node.config.four_eyes !== false
  const branches = node.config.branches ?? []
  const exits = kind === 'decision'
    ? [...branches.map(b => ({ id: b.id, label: b.label || 'Branch' })), ...(node.config.otherwise !== false ? [{ id: 'otherwise', label: node.config.otherwise_label || 'Otherwise' }] : [])]
    : []
  return (
    <div className={cn('cf-node', `tone-${tone}`, `kind-${kind}`, selected && 'is-selected', errors > 0 && 'has-error', !errors && warnings > 0 && 'has-warning',
      trial === 'current' && 'is-current', trial === 'visited' && 'is-visited', dropTarget && 'is-drop')}
      aria-label={`${block?.label ?? node.type}: ${node.config.label ?? ''}`}>
      {kind !== 'start' && <Handle type="target" position={Position.Top} className="cf-handle" />}
      <div className="cf-node-h">
        <span className="cf-node-icon"><CatalogIcon name={icon} /></span>
        <span className="cf-node-kind">{block?.label ?? node.type}</span>
        {(errors > 0 || warnings > 0) && (
          <span className={cn('cf-badge', errors ? 'err' : 'warn')} title={issues.map(i => i.message).join('\n')}>
            {errors ? <CircleAlert size={12} /> : <AlertTriangle size={12} />}{errors || warnings}
          </span>
        )}
      </div>
      <div className="cf-node-title">{node.config.label || <span className="muted">Unnamed</span>}</div>
      <div className="cf-node-sub">{summary}</div>
      {(tasks > 0 || four || (node.config.actions?.length ?? 0) > 0) && (
        <div className="cf-node-chips">
          {tasks > 0 && <span className="cf-chip"><ListChecks size={11} />{tasks} task{tasks > 1 ? 's' : ''}</span>}
          {four && <span className="cf-chip"><Lock size={11} />four-eyes</span>}
          {(node.config.actions ?? []).map((a, i) => {
            const spec = actions.find(x => x.id === a.do)
            return <span key={i} className="cf-chip auto" title={spec?.description}><CatalogIcon name={spec?.icon} size={11} />{spec?.label ?? a.do}</span>
          })}
        </div>
      )}
      {kind === 'decision' && (
        <div className="cf-exits">
          {exits.map(x => (
            <div key={x.id} className={cn('cf-exit', x.id === 'otherwise' && 'otherwise')}>
              <span>{x.label}</span>
              <Handle type="source" id={x.id} position={Position.Bottom} className="cf-handle exit" />
            </div>
          ))}
          {exits.length === 0 && <div className="cf-exit muted">Add a branch</div>}
        </div>
      )}
      {kind !== 'outcome' && kind !== 'decision' && <Handle type="source" position={Position.Bottom} className="cf-handle" />}
    </div>
  )
}

export default memo(BlockNode)
