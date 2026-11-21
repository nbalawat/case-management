import { memo } from 'react'
import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, useInternalNode, type Edge, type EdgeProps } from '@xyflow/react'
import { CircleAlert, Filter, Lock } from 'lucide-react'
import { cn } from '../../../lib/cn'
import type { DEdge, Issue } from '../types'

export type StepData = { edge: DEdge; route: boolean; issues: Issue[]; trial?: boolean; who: string; stack?: number }
export type StepFlowEdge = Edge<StepData, 'step'>

/** A step between blocks. Steps people take show their name and who takes them; routes out of a start or a decision stay quiet. */
function StepEdge({ id, source, target, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected, markerEnd }: EdgeProps<StepFlowEdge>) {
  const s = useInternalNode(source), t = useInternalNode(target)
  const back = targetY < sourceY - 10
  const [path, mx, my] = back
    ? returnPath(sourceX, sourceY, targetX, targetY, s?.internals.positionAbsolute.x, s?.measured.width, t?.internals.positionAbsolute.x, t?.measured.width)
    : getSmoothStepPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, borderRadius: 14, offset: 22 })
  // Labels sit just above where a step arrives, so several steps leaving one block never pile up in the middle.
  // Steps arriving at the same block stack upwards.
  const x = back ? mx : targetX
  const y = back ? my : Math.max(targetY - 26 - (data?.stack ?? 0) * 24, Math.min(sourceY, targetY) + 12)
  const e = data?.edge
  const errors = (data?.issues ?? []).filter(i => i.level === 'error').length
  const conditional = !!e?.config.when?.conditions?.length
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd}
        className={cn('cf-edge', selected && 'is-selected', errors > 0 && 'has-error', data?.trial && 'is-trial', data?.route && 'is-route')} />
      {e && !data?.route && (
        <EdgeLabelRenderer>
          <div className={cn('cf-edge-label nodrag nopan', selected && 'is-selected', errors > 0 && 'has-error')}
            style={{ transform: `${back ? 'translate(8px, -50%)' : 'translate(-50%, -50%)'} translate(${x}px, ${y}px)` }}>
            {errors > 0 && <CircleAlert size={11} />}
            {e.config.four_eyes && <Lock size={11} aria-label="four-eyes" />}
            {conditional && <Filter size={11} aria-label="only when" />}
            <span>{e.config.label || 'Unnamed step'}</span>
            {data?.who && <em>{data.who}</em>}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

/**
 * A step back up the flow (rework, reopen) goes round the right of both blocks instead of over the steps going down,
 * so it never hides them. Returns the path and where its label sits.
 */
function returnPath(sx: number, sy: number, tx: number, ty: number, sLeft = sx, sWidth = 0, tLeft = tx, tWidth = 0): [string, number, number] {
  const side = Math.max(sLeft + sWidth, tLeft + tWidth, sx, tx) + 36
  const below = sy + 18, above = ty - 18, r = 10
  const path = [
    `M ${sx} ${sy}`, `L ${sx} ${below - r}`, `Q ${sx} ${below} ${sx + r} ${below}`,
    `L ${side - r} ${below}`, `Q ${side} ${below} ${side} ${below - r}`,
    `L ${side} ${above + r}`, `Q ${side} ${above} ${side - r} ${above}`,
    `L ${tx + r} ${above}`, `Q ${tx} ${above} ${tx} ${above + r}`, `L ${tx} ${ty}`,
  ].join(' ')
  return [path, side, (below + above) / 2]
}

export default memo(StepEdge)
