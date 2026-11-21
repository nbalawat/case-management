import { getPersonaId, getTenantId } from '../../api'

/**
 * Per-person grid preferences (visible columns, density, saved views), kept in this browser and keyed by client,
 * person and grid, so two people on one laptop in a demo do not share them. Server-side storage would let views
 * be shared across devices; that is a later step.
 */
const key = (grid: string, what: string) => `grid:${getTenantId()}:${getPersonaId()}:${grid}:${what}`

export function load<T>(grid: string, what: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key(grid, what))
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch { return fallback }
}

export function save(grid: string, what: string, value: unknown) {
  try { localStorage.setItem(key(grid, what), JSON.stringify(value)) } catch { /* storage full or private mode */ }
}

export type SavedView = { name: string; query: string }
