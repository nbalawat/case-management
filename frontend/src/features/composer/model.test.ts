import { describe, expect, it } from 'vitest'
import { addBlock, attachAction, connect, defaultStep, duplicate, freshLabel, record, redo, removeBranch, removeNodes, setOtherwise, undo } from './model'
import type { Catalog, Design } from './types'

const field = (key: string) => ({ key, label: key, kind: 'text' as const, required: false, options: [], default: null, help: '' })
const block = (id: string, kind: 'start' | 'stage' | 'decision' | 'outcome', accepts_actions = false) =>
  ({ id, kind, category: 'x', label: id, description: '', icon: 'zap', fields: [field('label')], defaults: { label: id === 'stage' ? 'Review' : id }, accepts_actions, accepts_tasks: kind === 'stage', presets: [] })

const cat = {
  categories: [], step_fields: [], branch_step_fields: [], formats: [],
  blocks: [block('start', 'start'), block('stage', 'stage', true), block('approval', 'stage', true), block('decision', 'decision'), block('outcome', 'outcome')],
  actions: [{ id: 'notify', label: 'Notify', description: '', icon: 'bell', fields: [field('role'), { ...field('message'), default: 'Hi' }] }],
  reference: {
    roles: [{ id: 'analyst', label: 'Analyst', people: ['Ana'] }, { id: 'team_lead', label: 'Team lead', people: ['Tara'] }, { id: 'mlro', label: 'MLRO', people: ['Maya'] }],
    queues: [{ id: 'l1', label: 'Triage', roles: ['analyst'], people: ['Ana'] }],
    requirements: [], facts: [], agents: [], recipients: [], scenarios: [], dispositions: [], autonomy: [], forms: [], decision_tables: [], live: [],
  },
} as unknown as Catalog

const base = (): Design => ({
  key: 'wf', title: 'Wf', domain: '', description: '', version: '1.0.0',
  settings: { deadline_label: 'x', deadline_days: 30, escalation_raise_priority: true, escalation_notify: 'team_lead', qa_sample_rate: 0, qa_dispositions: [], qa_reopen_to: 's1', agents: [], autonomy: 'L2' },
  nodes: [
    { id: 'start', type: 'start', position: { x: 0, y: 0 }, config: { label: 'Case opened' } },
    { id: 's1', type: 'stage', position: { x: 300, y: 0 }, config: { label: 'Review', queue: 'l1', tasks: [{ label: 't' }] } },
    { id: 'ap', type: 'approval', position: { x: 600, y: 0 }, config: { label: 'Approval', approvers: ['mlro'], four_eyes: true } },
    { id: 'd', type: 'decision', position: { x: 600, y: 200 }, config: { label: 'D', branches: [{ id: 'b1', label: 'Big', conditions: [] }], otherwise: true } },
    { id: 'o', type: 'outcome', position: { x: 900, y: 0 }, config: { label: 'Closed', disposition: 'closed' } },
  ],
  edges: [{ id: 'e0', source: 'start', target: 's1', config: {} }],
})

describe('adding blocks', () => {
  it('never adds a second start, and names new blocks uniquely', () => {
    expect(addBlock(base(), cat, 'start', undefined, { x: 0, y: 0 }).id).toBeNull()
    const r = addBlock(base(), cat, 'stage', undefined, { x: 1, y: 2 })
    expect(r.design.nodes.at(-1)?.config.label).toBe('Review 2')
    expect(freshLabel(base(), 'Nothing')).toBe('Nothing')
  })
})

describe('connecting', () => {
  it('fills a step with sensible defaults from the stage', () => {
    const d = base()
    const r = connect(d, cat, 's1', 'ap')
    const e = r.design.edges.find(x => x.id === r.id)!
    expect(e.config).toEqual({ label: 'Submit for approval', roles: ['analyst'], requires: ['tasks'] })
  })
  it('defaults steps out of an approval to its approvers with four-eyes, and returns without it', () => {
    const d = base()
    expect(defaultStep(cat, d.nodes[2], d.nodes[4])).toEqual({ label: 'Close as closed', roles: ['mlro'], requires: ['note'], four_eyes: true })
    expect(defaultStep(cat, d.nodes[2], d.nodes[1], connect(d, cat, 's1', 'ap').design)).toMatchObject({ label: 'Return for rework', four_eyes: false })
    expect(defaultStep(cat, d.nodes[2], d.nodes[1], d)).toMatchObject({ label: 'Approve' })
  })
  it('refuses what the platform never allows, with a reason', () => {
    const d = base()
    expect(connect(d, cat, 'o', 's1').why).toMatch(/closes the case/)
    expect(connect(d, cat, 's1', 'start').why).toMatch(/start/)
    expect(connect(d, cat, 'start', 'ap').why).toMatch(/already leads/)
    expect(connect(d, cat, 's1', 's1').why).toMatch(/same block/)
    const once = connect(d, cat, 's1', 'o').design
    expect(connect(once, cat, 's1', 'o').why).toMatch(/already joined/)
  })
  it('gives each decision branch exactly one route', () => {
    let d = connect(base(), cat, 'd', 'o', 'b1').design
    d = connect(d, cat, 'd', 'ap', 'b1').design
    expect(d.edges.filter(e => e.source === 'd' && e.handle === 'b1').map(e => e.target)).toEqual(['ap'])
    expect(connect(d, cat, 'd', 'o').design.edges.some(e => e.handle === 'otherwise')).toBe(true)
  })
})

describe('changing and removing', () => {
  it('removes steps with their blocks, clears settings that pointed there, and keeps the start', () => {
    const d = removeNodes(connect(base(), cat, 's1', 'o').design, ['s1', 'start'])
    expect(d.nodes.map(n => n.id)).toContain('start')
    expect(d.edges).toEqual([])
    expect(d.settings.qa_reopen_to).toBeNull()
  })
  it('drops a branch with its route, and Otherwise with its route', () => {
    let d = connect(connect(base(), cat, 'd', 'o', 'b1').design, cat, 'd', 'ap', 'otherwise').design
    d = removeBranch(d, 'd', 'b1')
    expect(d.edges.some(e => e.handle === 'b1')).toBe(false)
    d = setOtherwise(d, 'd', false)
    expect(d.edges.some(e => e.source === 'd')).toBe(false)
  })
  it('attaches actions to stages only, with their defaults', () => {
    const ok = attachAction(base(), cat, 's1', 'notify').design
    expect(ok.nodes[1].config.actions).toEqual([{ do: 'notify', message: 'Hi', role: 'team_lead' }])
    expect(attachAction(base(), cat, 'o', 'notify').why).toMatch(/stages/)
  })
  it('duplicates blocks and the steps between them with fresh ids', () => {
    const d = connect(base(), cat, 's1', 'ap').design
    const r = duplicate(d, ['s1', 'ap'])
    expect(r.ids).toHaveLength(2)
    expect(r.design.edges).toHaveLength(d.edges.length + 1)
    expect(r.design.nodes.filter(n => n.config.label?.startsWith('Review'))).toHaveLength(2)
  })
})

describe('history', () => {
  it('undoes and redoes, and a new change clears redo', () => {
    const a = base(), b = { ...a, title: 'B' }, c = { ...a, title: 'C' }
    let h = record(record({ past: [], present: a, future: [] }, b), c)
    h = undo(h)
    expect(h.present.title).toBe('B')
    expect(redo(h).present.title).toBe('C')
    h = record(h, { ...a, title: 'D' })
    expect(h.future).toEqual([])
    expect(undo(undo(undo(h))).present).toBe(a)
  })
})
