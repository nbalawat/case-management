import { describe, expect, it } from 'vitest'
import { daily, grouped, pick, totals, visible } from './txns'
import type { Txn } from './types'

const t = (id: string, ts: string, amount: number, direction = 'in', extra: Partial<Txn> = {}): Txn =>
  ({ id, ts, amount, direction, channel: 'wire', counterparty: 'Acme', country: 'US', alerted: false, ...extra })

const rows = [
  t('a', '2026-03-01T10:00:00', 100, 'in', { alerted: true }),
  t('b', '2026-03-03T09:00:00', 40, 'out', { counterparty: '', channel: 'cash' }),
  t('c', '2026-03-03T12:00:00', 60, 'in', { country: 'AE', counterparty: 'Gulf Ltd', alerted: true }),
]

describe('daily', () => {
  it('fills empty days between the first and last transaction', () => {
    const d = daily(rows)
    expect(d.map(x => x.day)).toEqual(['2026-03-01', '2026-03-02', '2026-03-03'])
    expect(d[1].count).toBe(0)
    expect(d[2]).toMatchObject({ in: 60, out: 40, count: 2, alerted: 1 })
  })
  it('is empty without transactions', () => expect(daily([])).toEqual([]))
})

describe('grouped', () => {
  it('labels cash and orders by total value', () => {
    const g = grouped(rows, 'counterparty')
    expect(g.map(x => x.key)).toEqual(['Acme', 'Gulf Ltd', 'Cash'])
    expect(g[0]).toMatchObject({ count: 1, in: 100, alerted: 1, ids: ['a'] })
  })
})

describe('visible', () => {
  const none = { focus: [], onlyAlerted: false, range: null, group: null }
  it('shows cited records regardless of the other filters', () =>
    expect(visible(rows, { ...none, onlyAlerted: true, focus: ['b'] }).map(x => x.id)).toEqual(['b']))
  it('combines alerted, day range and group', () => {
    expect(visible(rows, { ...none, onlyAlerted: true }).map(x => x.id)).toEqual(['a', 'c'])
    expect(visible(rows, { ...none, range: ['2026-03-02', '2026-03-03'] }).map(x => x.id)).toEqual(['b', 'c'])
    expect(visible(rows, { ...none, group: { by: 'country', key: 'AE' } }).map(x => x.id)).toEqual(['c'])
  })
  it('totals in and out', () => expect(totals(rows)).toEqual({ in: 160, out: 40, count: 3 }))
})

describe('pick', () => {
  it('starts, toggles off and extends a range', () => {
    expect(pick(null, '2026-03-02', false)).toEqual(['2026-03-02', '2026-03-02'])
    expect(pick(['2026-03-02', '2026-03-02'], '2026-03-02', false)).toBeNull()
    expect(pick(['2026-03-02', '2026-03-02'], '2026-03-05', true)).toEqual(['2026-03-02', '2026-03-05'])
    expect(pick(['2026-03-02', '2026-03-05'], '2026-03-01', true)).toEqual(['2026-03-01', '2026-03-05'])
  })
})
