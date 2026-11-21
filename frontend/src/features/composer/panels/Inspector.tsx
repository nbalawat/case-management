import { AlertTriangle, CircleAlert, Copy, GripVertical, Plus, Trash2, Users } from 'lucide-react'
import { cn } from '../../../lib/cn'
import { ConditionBuilder, Field, FieldControl, MultiSelect } from '../fields/FieldControl'
import { CatalogIcon } from '../CatalogIcon'
import { blockOf, kindOf, newId, nodeById, removeBranch, setOtherwise, updateEdge, updateNode } from '../model'
import type { Action, Catalog, DEdge, Design, DNode, Issue, Settings } from '../types'

export type Selection = { kind: 'node' | 'edge'; id: string } | null

type Props = {
  cat: Catalog; design: Design; selection: Selection; issues: Issue[]; readOnly: boolean
  onChange: (d: Design) => void; onDelete: () => void; onDuplicate: () => void; onSelect: (s: Selection) => void
}

function Issues({ items }: { items: Issue[] }) {
  if (!items.length) return null
  return (
    <ul className="cf-issues-inline">
      {items.map((i, k) => (
        <li key={k} className={i.level}>
          {i.level === 'error' ? <CircleAlert size={14} /> : <AlertTriangle size={14} />}
          <div><b>{i.message}</b>{i.hint && <span>{i.hint}</span>}</div>
        </li>
      ))}
    </ul>
  )
}

function Tasks({ node, onChange, disabled }: { node: DNode; onChange: (tasks: DNode['config']['tasks']) => void; disabled: boolean }) {
  const tasks = node.config.tasks ?? []
  const set = (i: number, patch: object) => onChange(tasks.map((t, j) => (j === i ? { ...t, ...patch } : t)))
  const move = (i: number, d: number) => {
    const j = i + d
    if (j < 0 || j >= tasks.length) return
    const next = [...tasks];
    [next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }
  return (
    <div className="cf-section">
      <h4>Checklist <span className="muted small">what the team ticks off here</span></h4>
      {tasks.length === 0 && <p className="cf-help">No tasks. Add the checks a careful analyst would make.</p>}
      <ol className="cf-tasks">
        {tasks.map((t, i) => (
          <li key={i}>
            <span className="cf-grip" title="Move with the arrows"><GripVertical size={14} /></span>
            <input aria-label={`Task ${i + 1}`} value={t.label} disabled={disabled} placeholder="Describe the task" onChange={e => set(i, { label: e.target.value })}
              onKeyDown={e => { if (e.altKey && e.key === 'ArrowUp') move(i, -1); if (e.altKey && e.key === 'ArrowDown') move(i, 1) }} />
            <label className="cf-mini" title="Required tasks must be ticked before a step that needs them"><input type="checkbox" checked={t.required !== false} disabled={disabled}
              onChange={e => set(i, { required: e.target.checked })} />required</label>
            <button type="button" className="ghost cf-icon-btn" aria-label="Remove task" disabled={disabled} onClick={() => onChange(tasks.filter((_, j) => j !== i))}><Trash2 size={14} /></button>
          </li>
        ))}
      </ol>
      <button type="button" className="ghost cf-add" disabled={disabled} onClick={() => onChange([...tasks, { label: '', required: true }])}><Plus size={14} />Add a task</button>
    </div>
  )
}

function Actions({ cat, node, onChange, disabled }: { cat: Catalog; node: DNode; onChange: (a: Action[]) => void; disabled: boolean }) {
  const actions = node.config.actions ?? []
  return (
    <div className="cf-section">
      <h4>When a case arrives <span className="muted small">automatic actions, in order</span></h4>
      {actions.length === 0 && <p className="cf-help">Nothing happens automatically. Drag an action from the palette onto the stage, or add one here.</p>}
      {actions.map((a, i) => {
        const spec = cat.actions.find(x => x.id === a.do)
        return (
          <div key={i} className="cf-action">
            <div className="cf-action-h"><CatalogIcon name={spec?.icon} size={14} /><b>{spec?.label ?? a.do}</b>
              <button type="button" className="ghost cf-icon-btn" aria-label={`Remove ${spec?.label ?? a.do}`} disabled={disabled} onClick={() => onChange(actions.filter((_, j) => j !== i))}><Trash2 size={14} /></button></div>
            {spec && <p className="cf-help">{spec.description}</p>}
            {spec?.fields.map(f => <FieldControl key={f.key} cat={cat} spec={f} value={a[f.key]} disabled={disabled}
              onChange={v => onChange(actions.map((x, j) => (j === i ? { ...x, [f.key]: v } : x)))} />)}
          </div>
        )
      })}
      <select className="cf-add-select" aria-label="Add an automatic action" value="" disabled={disabled}
        onChange={e => { if (e.target.value) onChange([...actions, defaultAction(cat, e.target.value)]) }}>
        <option value="">+ Add an automatic action…</option>
        {cat.actions.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}
      </select>
    </div>
  )
}

function defaultAction(cat: Catalog, id: string): Action {
  const spec = cat.actions.find(a => a.id === id)!
  const a: Action = { do: id }
  for (const f of spec.fields) if (f.default !== null && f.default !== undefined && f.default !== '') a[f.key] = f.default
  if (id === 'notify') a.role = cat.reference.roles.find(r => r.id === 'team_lead')?.id ?? cat.reference.roles[0]?.id
  return a
}

function Branches({ cat, design, node, onChange, disabled }: { cat: Catalog; design: Design; node: DNode; onChange: (d: Design) => void; disabled: boolean }) {
  const branches = node.config.branches ?? []
  const target = (handle: string) => {
    const e = design.edges.find(x => x.source === node.id && x.handle === handle)
    return e ? nodeById(design, e.target)?.config.label ?? '…' : null
  }
  const set = (i: number, patch: object) => onChange(updateNode(design, node.id, { branches: branches.map((b, j) => (j === i ? { ...b, ...patch } : b)) }))
  return (
    <div className="cf-section">
      <h4>Branches <span className="muted small">checked in order; the first that holds wins</span></h4>
      {branches.map((b, i) => (
        <div key={b.id} className="cf-branch">
          <div className="cf-branch-h">
            <input aria-label={`Branch ${i + 1} name`} value={b.label} placeholder="Name this branch" disabled={disabled} onChange={e => set(i, { label: e.target.value })} />
            <button type="button" className="ghost cf-icon-btn" aria-label="Remove branch" disabled={disabled} onClick={() => onChange(removeBranch(design, node.id, b.id))}><Trash2 size={14} /></button>
          </div>
          <ConditionBuilder cat={cat} group={b} disabled={disabled} empty="No condition yet: add one." onChange={g => set(i, { match: g.match, conditions: g.conditions })} />
          <p className={cn('cf-help', !target(b.id) && 'bad')}>{target(b.id) ? `Goes to ${target(b.id)}` : 'Goes nowhere yet: drag from its dot on the canvas to the next block.'}</p>
        </div>
      ))}
      <button type="button" className="ghost cf-add" disabled={disabled}
        onClick={() => onChange(updateNode(design, node.id, { branches: [...branches, { id: newId('b'), label: '', match: 'all', conditions: [] }] }))}><Plus size={14} />Add a branch</button>
      <div className="cf-branch otherwise">
        <label className="cf-switch"><input type="checkbox" checked={node.config.otherwise !== false} disabled={disabled} onChange={e => onChange(setOtherwise(design, node.id, e.target.checked))} />
          <span>Otherwise: every case no branch takes</span></label>
        {node.config.otherwise !== false && <>
          <input aria-label="Otherwise name" value={node.config.otherwise_label ?? ''} placeholder="Otherwise" disabled={disabled}
            onChange={e => onChange(updateNode(design, node.id, { otherwise_label: e.target.value }))} />
          <p className={cn('cf-help', !target('otherwise') && 'bad')}>{target('otherwise') ? `Goes to ${target('otherwise')}` : 'Goes nowhere yet.'}</p>
        </>}
      </div>
    </div>
  )
}

function NodePanel({ cat, design, node, issues, readOnly, onChange, onDelete, onDuplicate }: Props & { node: DNode }) {
  const block = blockOf(cat, node.type)
  if (!block) return <Issues items={issues} />
  const set = (patch: object) => onChange(updateNode(design, node.id, patch))
  const fields = block.fields.filter(f => !(f.key === 'approvers' && node.type !== 'approval'))
  return (
    <>
      <div className="cf-insp-h"><span className={`cf-node-icon tone-${node.type}`}><CatalogIcon name={block.icon} size={16} /></span>
        <div><div className="cf-insp-kind">{block.label}</div><p className="cf-help">{block.description}</p></div></div>
      <Issues items={issues} />
      {fields.map(f => <FieldControl key={f.key} cat={cat} spec={f} value={node.config[f.key]} disabled={readOnly} onChange={v => set({ [f.key]: v })} />)}
      {node.type === 'approval' && (
        <Field label="Four-eyes on approving steps" help="New steps out of this approval start with four-eyes on. You can still change each one.">
          <label className="cf-switch"><input type="checkbox" checked={node.config.four_eyes !== false} disabled={readOnly} onChange={e => set({ four_eyes: e.target.checked })} /><span>{node.config.four_eyes !== false ? 'On' : 'Off'}</span></label>
        </Field>
      )}
      {node.config.queue && <QueuePeople cat={cat} queue={node.config.queue} />}
      {block.kind === 'decision' && <Branches cat={cat} design={design} node={node} onChange={onChange} disabled={readOnly} />}
      {block.accepts_tasks && <Tasks node={node} disabled={readOnly} onChange={tasks => set({ tasks })} />}
      {block.accepts_actions && <Actions cat={cat} node={node} disabled={readOnly} onChange={actions => set({ actions })} />}
      {!readOnly && <div className="cf-insp-foot">
        {block.kind !== 'start' && <button type="button" onClick={onDuplicate}><Copy size={14} />Duplicate</button>}
        {block.kind !== 'start' && <button type="button" className="danger" onClick={onDelete}><Trash2 size={14} />Delete</button>}
      </div>}
    </>
  )
}

function QueuePeople({ cat, queue }: { cat: Catalog; queue: string }) {
  const q = cat.reference.queues.find(x => x.id === queue)
  if (!q) return null
  return <p className="cf-help cf-people"><Users size={13} />{q.people.length ? `${q.people.join(', ')} work${q.people.length === 1 ? 's' : ''} this queue` : 'Nobody works this queue yet'}</p>
}

function EdgePanel({ cat, design, edge, issues, readOnly, onChange, onDelete }: Props & { edge: DEdge }) {
  const src = nodeById(design, edge.source), dst = nodeById(design, edge.target)
  const route = kindOf(cat, src) !== 'stage'
  const set = (patch: object) => onChange(updateEdge(design, edge.id, patch))
  const roles = edge.config.roles?.length ? edge.config.roles : src?.type === 'approval' ? src.config.approvers ?? [] : []
  const people = [...new Set(cat.reference.roles.filter(r => roles.includes(r.id)).flatMap(r => r.people))]
  return (
    <>
      <div className="cf-insp-h"><div><div className="cf-insp-kind">{route ? 'Route' : 'Step'}</div>
        <p className="cf-help">From <b>{src?.config.label ?? '…'}</b> to <b>{dst?.config.label ?? '…'}</b>.{' '}
          {route ? (kindOf(cat, src) === 'start' ? 'Every new case starts here.' : 'The decision takes this route when the branch holds; nobody presses a button.')
            : 'A button the people below can press, once its requirements are met.'}</p></div></div>
      <Issues items={issues} />
      {route ? cat.branch_step_fields.map(f => kindOf(cat, src) === 'decision' && <FieldControl key={f.key} cat={cat} spec={f} value={edge.config[f.key as 'label']} disabled={readOnly} onChange={v => set({ [f.key]: v })} />) : <>
        {cat.step_fields.map(f => <FieldControl key={f.key} cat={cat} spec={f}
          value={f.key === 'four_eyes' && edge.config.four_eyes === undefined && src?.type === 'approval' ? src.config.four_eyes !== false : edge.config[f.key as keyof DEdge['config']]}
          disabled={readOnly} onChange={v => set({ [f.key]: v })} />)}
        {!edge.config.roles?.length && src?.type === 'approval' && !!src.config.approvers?.length && <p className="cf-help">Uses the approval's approvers.</p>}
        <p className="cf-help cf-people"><Users size={13} />{people.length ? `${people.join(', ')} can take it` : 'Nobody can take it yet'}</p>
        <Field label="Only when" help="Leave empty to offer the step on every case. For routing by facts, a decision block is usually clearer.">
          <ConditionBuilder cat={cat} group={edge.config.when} disabled={readOnly} onChange={g => set({ when: g })} />
        </Field>
      </>}
      {!readOnly && <div className="cf-insp-foot"><button type="button" className="danger" onClick={onDelete}><Trash2 size={14} />Delete step</button></div>}
    </>
  )
}

function WorkflowPanel({ cat, design, issues, readOnly, onChange }: Props) {
  const s = design.settings
  const set = (patch: Partial<Settings>) => onChange({ ...design, settings: { ...s, ...patch } })
  const stages = design.nodes.filter(n => kindOf(cat, n) === 'stage')
  const dis = readOnly
  return (
    <>
      <div className="cf-insp-h"><div><div className="cf-insp-kind">Workflow</div><p className="cf-help">Select a block or a step to change it. These settings apply to the whole workflow.</p></div></div>
      <Issues items={issues} />
      <Field label="Name" required><input value={design.title} disabled={dis} onChange={e => onChange({ ...design, title: e.target.value })} /></Field>
      <div className="cf-row">
        <Field label="Key" help="Cases and reports use it. Lower case and underscores."><input value={design.key} disabled={dis}
          onChange={e => onChange({ ...design, key: e.target.value.toLowerCase().replace(/[^a-z0-9_]+/g, '_').slice(0, 58) })} /></Field>
        <Field label="Version"><input value={design.version} disabled={dis} onChange={e => onChange({ ...design, version: e.target.value })} /></Field>
      </div>
      <Field label="Domain" help="Groups it with the desk that works it, such as AML, KYC, Fraud or Sanctions."><input value={design.domain} disabled={dis} onChange={e => onChange({ ...design, domain: e.target.value })} /></Field>
      <Field label="What it is for"><textarea rows={3} value={design.description} disabled={dis} onChange={e => onChange({ ...design, description: e.target.value })} /></Field>
      <div className="cf-section"><h4>Deadline and escalation</h4>
        <div className="cf-row">
          <Field label="Deadline name"><input value={s.deadline_label} disabled={dis} onChange={e => set({ deadline_label: e.target.value })} /></Field>
          <Field label="Days"><input type="number" min={1} value={s.deadline_days} disabled={dis} onChange={e => set({ deadline_days: Number(e.target.value) || 1 })} /></Field>
        </div>
        <Field label="When a stage clock runs out"><label className="cf-switch"><input type="checkbox" checked={s.escalation_raise_priority} disabled={dis} onChange={e => set({ escalation_raise_priority: e.target.checked })} /><span>Raise the priority</span></label></Field>
        <Field label="…and tell"><select value={s.escalation_notify} disabled={dis} onChange={e => set({ escalation_notify: e.target.value })}>
          {cat.reference.roles.map(r => <option key={r.id} value={r.id}>{r.label}</option>)}</select></Field>
      </div>
      <div className="cf-section"><h4>Quality assurance</h4>
        <Field label={`Sample ${Math.round(s.qa_sample_rate * 100)}% of closed cases`}>
          <input type="range" min={0} max={100} step={5} value={Math.round(s.qa_sample_rate * 100)} disabled={dis} onChange={e => set({ qa_sample_rate: Number(e.target.value) / 100 })} aria-label="QA sample rate" />
        </Field>
        {s.qa_sample_rate > 0 && <>
          <Field label="Only these outcomes" help="Leave empty to sample every outcome.">
            <MultiSelect label="QA outcomes" options={[...new Set(design.nodes.filter(n => n.type === 'outcome').map(n => n.config.disposition ?? ''))].filter(Boolean).map(d => ({ id: d, label: d.replaceAll('_', ' ') }))}
              value={s.qa_dispositions} onChange={v => set({ qa_dispositions: v })} disabled={dis} />
          </Field>
          <Field label="A failed review reopens the case at"><select value={s.qa_reopen_to ?? ''} disabled={dis} onChange={e => set({ qa_reopen_to: e.target.value || null })}>
            <option value="">Do not reopen</option>{stages.map(n => <option key={n.id} value={n.id}>{n.config.label}</option>)}</select></Field>
        </>}
      </div>
      <div className="cf-section"><h4>Agents</h4>
        <Field label="Prepare every new case with" help="Agents gather evidence and recommend. People decide every step.">
          <MultiSelect label="Agents" options={cat.reference.agents} value={s.agents} onChange={v => set({ agents: v })} disabled={dis} />
        </Field>
        <Field label="Autonomy" help={cat.reference.autonomy.find(a => a.id === s.autonomy)?.help}>
          <select value={s.autonomy} disabled={dis} onChange={e => set({ autonomy: e.target.value })}>{cat.reference.autonomy.map(a => <option key={a.id} value={a.id}>{a.label}</option>)}</select>
        </Field>
      </div>
    </>
  )
}

export default function Inspector(props: Props) {
  const { design, selection, issues } = props
  const node = selection?.kind === 'node' ? nodeById(design, selection.id) : undefined
  const edge = selection?.kind === 'edge' ? design.edges.find(e => e.id === selection.id) : undefined
  const mine = issues.filter(i => (node ? i.target === 'node' && i.id === node.id : edge ? i.target === 'edge' && i.id === edge.id : i.target === 'workflow'))
  return (
    <aside className="cf-inspector" aria-label="Inspector">
      {node ? <NodePanel {...props} node={node} issues={mine} /> : edge ? <EdgePanel {...props} edge={edge} issues={mine} /> : <WorkflowPanel {...props} issues={mine} />}
    </aside>
  )
}
