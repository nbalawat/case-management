import { useState } from 'react'
import { Search } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { CatalogIcon } from '../CatalogIcon'
import { toneOf } from '../icons'
import type { Catalog, Preset } from '../types'

export const BLOCK_MIME = 'application/x-caseforge-block'
export const ACTION_MIME = 'application/x-caseforge-action'

type Props = {
  cat: Catalog
  readOnly: boolean
  hasStart: boolean
  canAttach: boolean
  onAdd: (type: string, preset: Preset) => void
  onAttach: (actionId: string) => void
}

/** Ready-made blocks, grouped by what they are for. Drag one onto the canvas, or click to add it beside the selection. */
export default function Palette({ cat, readOnly, hasStart, canAttach, onAdd, onAttach }: Props) {
  const [q, setQ] = useState('')
  const match = (s: string) => !q || s.toLowerCase().includes(q.toLowerCase())
  const groups = cat.categories.filter(c => c !== 'Automatic actions').map(c => ({
    title: c,
    items: cat.blocks.filter(b => b.category === c).flatMap(b => b.presets.map(p => ({ block: b, preset: p })))
      .filter(({ preset, block }) => match(`${preset.label} ${preset.description} ${block.label}`)),
  })).filter(g => g.items.length)
  const actions = cat.actions.filter(a => match(`${a.label} ${a.description}`))
  return (
    <aside className="cf-palette" aria-label="Building blocks">
      <div className="cf-search"><Search size={14} aria-hidden /><input type="search" placeholder="Find a block" value={q} onChange={e => setQ(e.target.value)} aria-label="Find a block" /></div>
      {groups.map(g => (
        <section key={g.title}>
          <h4>{g.title}</h4>
          {g.items.map(({ block, preset }) => {
            const disabled = readOnly || (block.kind === 'start' && hasStart)
            return (
              <button key={`${block.id}:${preset.id}`} type="button" className={cn('cf-pal-item', `tone-${toneOf(block.id, block.kind)}`)} disabled={disabled} draggable={!disabled}
                title={block.kind === 'start' && hasStart ? 'This workflow already has its start' : preset.description}
                onDragStart={e => { e.dataTransfer.setData(BLOCK_MIME, JSON.stringify({ type: block.id, preset: preset.id })); e.dataTransfer.effectAllowed = 'copy' }}
                onClick={() => onAdd(block.id, preset)}>
                <span className="cf-node-icon"><CatalogIcon name={preset.icon} /></span>
                <span className="cf-pal-text"><b>{preset.label}</b><span>{preset.description}</span></span>
              </button>
            )
          })}
        </section>
      ))}
      {actions.length > 0 && (
        <section>
          <h4>Automatic actions <span className="muted small">drop on a stage</span></h4>
          {actions.map(a => {
            return (
              <button key={a.id} type="button" className="cf-pal-item tone-auto" disabled={readOnly} draggable={!readOnly}
                title={canAttach ? `Add to the selected stage. ${a.description}` : a.description}
                onDragStart={e => { e.dataTransfer.setData(ACTION_MIME, a.id); e.dataTransfer.effectAllowed = 'copy' }}
                onClick={() => onAttach(a.id)}>
                <span className="cf-node-icon"><CatalogIcon name={a.icon} /></span>
                <span className="cf-pal-text"><b>{a.label}</b><span>{a.description}</span></span>
              </button>
            )
          })}
        </section>
      )}
      {(cat.reference.forms.length > 0 || cat.reference.decision_tables.length > 0) && (
        <p className="cf-help">Decision tables appear as facts in decisions; forms are chosen on a stage.</p>
      )}
    </aside>
  )
}
