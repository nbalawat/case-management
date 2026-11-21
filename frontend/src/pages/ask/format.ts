import { words } from '../../api'
import type { Unit } from './types'

/** Who answered, in words: the offline planner, or the endpoint and the model that served the answer. */
export const providerLabel = (p: string) => {
  if (!p || p.startsWith('offline')) return 'Offline planner (no model connected)'
  const [endpoint, model] = p.split(':')
  return model ? `Model: ${model} (${endpoint === 'anthropic' ? 'Anthropic API' : endpoint === 'openai' ? 'open-weight server' : endpoint})` : `Model: ${p}`
}

const OLD_UNIT: Record<string, Unit> = { share: 'percent', agent_agreement: 'percent' }

/** A value as the answer would write it. `unit` comes from the result; older case results only name the measure. */
export function show(v: unknown, unit: Unit = '', measure = ''): string {
  if (v === null || v === undefined || v === '') return '–'
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v !== 'number') return String(v)
  const u = unit || OLD_UNIT[measure] || (measure.includes('amount') ? 'usd' : '')
  if (u === 'usd') return Math.abs(v) < 100 ? `$${v.toLocaleString('en-US', { maximumFractionDigits: 2 })}` : `$${Math.round(v).toLocaleString('en-US')}`
  if (u === 'percent') return `${v}%`
  if (u === 'days') return `${v.toLocaleString('en-US')} days`
  return v.toLocaleString('en-US')
}

export const objectWords = (o?: string) => (o && o !== 'cases' ? words(o) : 'cases')

/** Platform record ids, which the answer text links when a lookup showed them. */
export const RECORD_ID = /\b((?:case|alr|hld|dsp|sar|ctr|str|risk|trg|run|scv|lbk)-[0-9a-f]{10})\b/
export const isRecordId = (s: string) => new RegExp(`^${RECORD_ID.source}$`).test(s)
