import { describe, expect, it } from 'vitest'
import { shellContext } from './access'
import { homeFor, menuFor } from './menu'
import { MODULES, moduleFor, place, type Module } from './registry'

// Permissions as the Meridian demo policy grants them (backend/access/full_service_bank.yaml).
const analyst = shellContext(['work'], false, ['AML', 'Sanctions'])
const mlro = shellContext(['work', 'view_team', 'view_restricted', 'restrict_case', 'submit_filings', 'approve_risk', 'manage_lists',
  'sanctions_decisions', 'info_sharing', 'tune_detection', 'approve_tuning'], false, ['AML', 'KYC', 'Sanctions'])
const admin = shellContext(['view_team', 'publish_workflow', 'run_timers', 'load_data', 'manage_access', 'manage_agents', 'resolve_entities',
  'manage_lists', 'tune_detection'], false, ['Sanctions'])
const auditor = shellContext(['view_team', 'view_restricted'], true, [])

const labels = (ctx: ReturnType<typeof shellContext>) => menuFor(ctx).flatMap(g => g.items.map(m => m.label))
const groups = (ctx: ReturnType<typeof shellContext>) => menuFor(ctx).map(g => g.id)

describe('role-shaped menu', () => {
  it('gives an analyst their work and their desks, and nothing to configure', () => {
    expect(labels(analyst)).toEqual(['My work', 'Cases', 'Alert intake', 'Sanctions', 'Ask', 'Knowledge library', 'Entities'])
    expect(groups(analyst)).not.toContain('admin')
    expect(groups(analyst)).not.toContain('oversight')
  })

  it('gives the MLRO oversight and the detection controls they approve', () => {
    const l = labels(mlro)
    expect(l).toEqual(expect.arrayContaining(['KYC', 'Sanctions', 'Regulatory reports', 'Dashboard', 'Management reports', 'Governance', 'Detection tuning']))
    expect(l).not.toContain('Data pipelines')
    expect(l).not.toContain('Fraud')
  })

  it('gives the platform admin the administration area and no personal work list', () => {
    const l = labels(admin)
    expect(l).toEqual(expect.arrayContaining(['Workflows', 'Access', 'Data pipelines', 'Integrations', 'Code lists']))
    expect(l).not.toContain('My work')
  })

  it('lands each person on a page they can use', () => {
    expect(homeFor(analyst)).toBe('/')
    expect(homeFor(mlro)).toBe('/')
    expect(homeFor(admin)).toBe('/overview')
    expect(homeFor(auditor)).toBe('/overview')
  })

  it('never shows an empty group', () => {
    for (const ctx of [analyst, mlro, admin, auditor, shellContext([], false, [])]) {
      for (const g of menuFor(ctx)) expect(g.items.length).toBeGreaterThan(0)
    }
  })
})

describe('registry', () => {
  it('has unique ids and paths', () => {
    const paths = MODULES.flatMap(m => [m.path, ...(m.children ?? []).map(c => c.path)])
    expect(new Set(MODULES.map(m => m.id)).size).toBe(MODULES.length)
    expect(new Set(paths).size).toBe(paths.length)
  })

  it('maps detail pages to the module that owns them', () => {
    expect(moduleFor('/cases/case-123')?.id).toBe('cases')
    expect(moduleFor('/customers/P10001')?.id).toBe('entities')
    expect(moduleFor('/overview/report')?.id).toBe('mi')
    expect(moduleFor('/overview')?.id).toBe('overview')
    expect(moduleFor('/')?.id).toBe('my-work')
    expect(moduleFor('/nowhere')).toBeUndefined()
  })
})

describe('one-file modules', () => {
  it('never repeats an id or a path', () => {
    const ids = MODULES.map(m => m.id)
    const paths = MODULES.flatMap(m => [m.path, ...(m.children ?? []).map(c => c.path)])
    expect(ids.filter((x, i) => ids.indexOf(x) !== i)).toEqual([])
    expect(paths.filter((x, i) => paths.indexOf(x) !== i)).toEqual([])
  })
})

describe('placing a module from src/modules/', () => {
  const m = (id: string, group: Module['group'], after?: string) => ({ ...MODULES[0], id, group, after }) as Module
  const core = [m('a', 'work'), m('b', 'work'), m('c', 'admin')]
  it('goes after the module it names', () => {
    expect(place(core, [m('x', 'work', 'a')]).map(x => x.id)).toEqual(['a', 'x', 'b', 'c'])
  })
  it('otherwise goes at the end of its group', () => {
    expect(place(core, [m('x', 'work')]).map(x => x.id)).toEqual(['a', 'b', 'x', 'c'])
    expect(place(core, [m('y', 'desks')]).map(x => x.id)).toEqual(['a', 'b', 'c', 'y'])
  })
})
