import type { Txn } from './types'

/** Pure helpers behind the transaction explorer, kept apart from the view so they are easy to test. */

export const dayOf = (ts: string) => ts.slice(0, 10)

export type Day = { day: string; in: number; out: number; count: number; alerted: number }

/** One bucket per calendar day from the first transaction to the last, empty days included so gaps show. */
export function daily(txns: Txn[]): Day[] {
  if (txns.length === 0) return []
  const by = new Map<string, Day>()
  for (const t of txns) {
    const k = dayOf(t.ts)
    const d = by.get(k) ?? { day: k, in: 0, out: 0, count: 0, alerted: 0 }
    d[t.direction === 'in' ? 'in' : 'out'] += t.amount
    d.count += 1
    if (t.alerted) d.alerted += 1
    by.set(k, d)
  }
  const keys = [...by.keys()].sort()
  const out: Day[] = []
  const last = new Date(`${keys[keys.length - 1]}T00:00:00Z`)
  for (let d = new Date(`${keys[0]}T00:00:00Z`); d <= last; d.setUTCDate(d.getUTCDate() + 1)) {
    const k = d.toISOString().slice(0, 10)
    out.push(by.get(k) ?? { day: k, in: 0, out: 0, count: 0, alerted: 0 })
  }
  return out
}

export type GroupKey = 'counterparty' | 'channel' | 'country'
export type Group = { key: string; count: number; in: number; out: number; alerted: number; ids: string[] }

export const groupLabel = (t: Txn, by: GroupKey) => (by === 'counterparty' ? t.counterparty || 'Cash' : t[by] || '–')

/** Totals per counterparty, channel or country, largest total value first. */
export function grouped(txns: Txn[], by: GroupKey): Group[] {
  const m = new Map<string, Group>()
  for (const t of txns) {
    const k = groupLabel(t, by)
    const g = m.get(k) ?? { key: k, count: 0, in: 0, out: 0, alerted: 0, ids: [] }
    g.count += 1
    g[t.direction === 'in' ? 'in' : 'out'] += t.amount
    if (t.alerted) g.alerted += 1
    g.ids.push(t.id)
    m.set(k, g)
  }
  return [...m.values()].sort((a, b) => b.in + b.out - (a.in + a.out))
}

export type TxnFilter = { focus: string[]; onlyAlerted: boolean; range: [string, string] | null; group: { by: GroupKey; key: string } | null }

/** Cited records win over every other filter: a citation means "show me exactly these". */
export function visible(txns: Txn[], f: TxnFilter): Txn[] {
  if (f.focus.length) return txns.filter(t => f.focus.includes(t.id))
  return txns.filter(t => (!f.onlyAlerted || t.alerted)
    && (!f.range || (dayOf(t.ts) >= f.range[0] && dayOf(t.ts) <= f.range[1]))
    && (!f.group || groupLabel(t, f.group.by) === f.group.key))
}

export const totals = (txns: Txn[]) => txns.reduce((a, t) => {
  a[t.direction === 'in' ? 'in' : 'out'] += t.amount
  a.count += 1
  return a
}, { in: 0, out: 0, count: 0 })

/** Extends a picked range to a new day (shift-click) or starts a new one. */
export function pick(range: [string, string] | null, day: string, extend: boolean): [string, string] | null {
  if (!extend || !range) return range && range[0] === day && range[1] === day ? null : [day, day]
  return [day < range[0] ? day : range[0], day > range[1] ? day : range[1]]
}
