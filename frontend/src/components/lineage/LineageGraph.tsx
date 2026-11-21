import { useMemo, useState } from 'react'
import { Boxes, Cog, Database, FolderOpen, Radio, Archive } from 'lucide-react'
import { connected, curve, layout, type LEdge, type LNode } from './layout'

const ICON = { table: Database, files: FolderOpen, topic: Radio, lake: Boxes, backup: Archive } as const
const KIND = { table: 'Operational table', files: 'Files in storage', topic: 'Kafka topic', lake: 'Iceberg table', backup: 'Backup' } as const
const n = (v: number) => v.toLocaleString()
/** Files are named by their whole path; the box shows the end of it, the tooltip the rest. */
const short = (p: { type: string; kind?: string; name: string }) =>
  p.type === 'job' ? (/^(pipeline|stream)\./.test(p.name) ? p.name.replace(/^[a-z]+\./, '') : p.name[0].toUpperCase() + p.name.slice(1).replace('.', ' '))
    : p.kind === 'files' ? p.name.split('/').slice(-2).join('/') : p.kind === 'topic' ? p.name.split('.').slice(-1)[0] : p.name
const JOB: Record<string, string> = { pipeline: 'Pipeline', stream: 'Stream consumer', lake: 'Lake', backup: 'Backup' }

type Props = { nodes: LNode[]; edges: LEdge[]; selected: string | null; onSelect: (id: string | null) => void }

/** Datasets and the jobs between them, left to right from where data arrives to where it ends up. Choosing a
 * node lights up everything it came from and everything made from it. */
export default function LineageGraph({ nodes, edges, selected, onSelect }: Props) {
  const { placed, width, height } = useMemo(() => layout(nodes, edges), [nodes, edges])
  const [hover, setHover] = useState<string | null>(null)
  const focus = hover ?? selected
  const lit = useMemo(() => (focus ? connected(focus, edges) : null), [focus, edges])
  return (
    <div className="lin-graph" role="figure" aria-label="Data lineage graph">
      <svg viewBox={`0 0 ${width} ${height + 24}`} style={{ width: '100%', maxWidth: width, height: 'auto' }}>
        <defs><marker id="lin-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L8,4 L0,8 z" /></marker></defs>
        {edges.map(e => {
          const a = placed.get(e.from), b = placed.get(e.to)
          if (!a || !b) return null
          const on = !lit || (lit.has(e.from) && lit.has(e.to))
          return (
            <g key={`${e.from}>${e.to}`} className={`lin-edge${on ? '' : ' dim'}`}>
              <path d={curve(a, b)} markerEnd="url(#lin-arrow)" />
              {e.rows ? <title>{`${n(e.rows)} rows in ${e.runs} run${e.runs === 1 ? '' : 's'}`}</title> : <title>{`read in ${e.runs} run${e.runs === 1 ? '' : 's'}`}</title>}
            </g>
          )
        })}
        {[...placed.values()].map(p => {
          const Icon = p.type === 'job' ? Cog : ICON[(p.kind ?? 'table') as keyof typeof ICON] ?? Database
          const on = !lit || lit.has(p.id)
          const sub = p.type === 'job'
            ? `${JOB[p.name.split('.')[0]] ?? 'Job'} · ${n(p.runs ?? 0)} run${p.runs === 1 ? '' : 's'}${p.failed ? ` · ${p.failed} failed` : ''}`
            : `${KIND[(p.kind ?? 'table') as keyof typeof KIND] ?? p.kind}${p.rows_written ? ` · ${n(p.rows_written)} rows` : ''}`
          return (
            <g key={p.id} transform={`translate(${p.x},${p.y})`} tabIndex={0} role="button" aria-pressed={selected === p.id}
              aria-label={`${p.type === 'job' ? 'Job' : 'Dataset'} ${p.name}`}
              className={`lin-node ${p.type}${p.kind ? ` k-${p.kind}` : ''}${selected === p.id ? ' sel' : ''}${on ? '' : ' dim'}${p.failed ? ' failed' : ''}`}
              onClick={() => onSelect(selected === p.id ? null : p.id)} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onSelect(selected === p.id ? null : p.id) } }}
              onMouseEnter={() => setHover(p.id)} onMouseLeave={() => setHover(null)}>
              <rect width={p.w} height={p.h} rx={p.type === 'job' ? 25 : 8} />
              <foreignObject x={10} y={7} width={p.w - 20} height={p.h - 14}>
                <div className="lin-label">
                  <Icon size={15} aria-hidden />
                  <span><b title={p.namespace ? `${p.namespace} / ${p.name}` : p.name}>{short(p)}</b><small>{sub}</small></span>
                </div>
              </foreignObject>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
