/**
 * Drill-through: every figure on a dashboard opens the cases behind it. These helpers build those addresses and
 * describe the extra filters on the case list, so a person can see and remove what a dashboard chose for them.
 */

export type Drill = {
  status?: 'open' | 'closed' | 'all'
  case_type?: string; state?: string; priority?: string
  due?: 'week' | 'overdue'
  scenario?: string
  /** A day (2026-11-21) or a month (2026-11). */
  opened?: string; closed?: string
}

export function casesHref(d: Drill): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(d)) if (v && !(k === 'status' && v === 'open')) q.set(k, v)
  const s = q.toString()
  return s ? `/cases?${s}` : '/cases'
}

/** Filters only a dashboard sets; the case list shows them as removable chips. */
export const DRILL_KEYS = ['due', 'scenario', 'opened', 'closed'] as const

const when = (v: string) => (v.length === 7
  ? new Date(`${v}-15T12:00:00`).toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
  : new Date(`${v}T12:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }))

export function drillChips(params: URLSearchParams, scenarioTitle: (s: string) => string = s => s): { key: string; label: string }[] {
  const out: { key: string; label: string }[] = []
  const due = params.get('due')
  if (due) out.push({ key: 'due', label: due === 'overdue' ? 'Past the deadline' : 'Deadline within 7 days' })
  for (const s of params.getAll('scenario')) out.push({ key: 'scenario', label: `Alert scenario: ${scenarioTitle(s)}` })
  const opened = params.get('opened'), closed = params.get('closed')
  if (opened) out.push({ key: 'opened', label: `Opened ${opened.length === 7 ? 'in' : 'on'} ${when(opened)}` })
  if (closed) out.push({ key: 'closed', label: `Closed ${closed.length === 7 ? 'in' : 'on'} ${when(closed)}` })
  return out
}
