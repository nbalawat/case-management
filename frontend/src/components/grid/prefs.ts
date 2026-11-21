import { useCallback, useEffect, useState } from 'react'
import { api, getPersonaId, getTenantId } from '../../api'
import { useApi } from '../../useApi'

/**
 * Per-person grid settings (visible columns, density) and saved views, kept on the server so they follow a person
 * between devices, and so a view can be shared with the team. This browser keeps a copy of the settings only so a
 * grid opens with them before the server answers. Views a browser kept before views moved to the server are
 * brought over once, the first time the grid opens.
 */
export type SavedView = { id?: string; name: string; query: string; shared?: boolean; owner?: string; owner_name?: string; mine?: boolean }
type GridPrefs = { settings: Record<string, unknown>; views: SavedView[] }

const key = (grid: string, what: string) => `grid:${getTenantId()}:${getPersonaId()}:${grid}:${what}`

function cached<T>(grid: string, what: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key(grid, what))
    return raw ? (JSON.parse(raw) as T) : fallback
  } catch { return fallback }
}

function cache(grid: string, what: string, value: unknown) {
  try { localStorage.setItem(key(grid, what), JSON.stringify(value)) } catch { /* storage full or private mode */ }
}

const path = (grid: string) => `/grids/${encodeURIComponent(grid)}`

export function useGridPrefs(grid: string) {
  const { data, error, reload } = useApi<GridPrefs>(path(grid))
  const [mine, setMine] = useState<Record<string, unknown>>({})  // settings changed in this session, ahead of the server

  // Views from before the server kept them: hand them over once, then forget the browser's copy.
  useEffect(() => {
    if (!data) return
    const old = cached<SavedView[]>(grid, 'views', [])
    if (old.length === 0) return
    api.post(`${path(grid)}/views/import`, { views: old }).then(() => {
      try { localStorage.removeItem(key(grid, 'views')) } catch { /* nothing kept */ }
      reload()
    }).catch(() => { /* try again next time the grid opens */ })
  }, [data, grid, reload])

  const setting = useCallback(<T,>(name: string, fallback: T): T => {
    if (name in mine) return mine[name] as T
    if (data && name in data.settings) return data.settings[name] as T
    return cached(grid, name, fallback)
  }, [mine, data, grid])

  const putSetting = useCallback((name: string, value: unknown) => {
    setMine(m => ({ ...m, [name]: value }))
    cache(grid, name, value)
    api.put(`${path(grid)}/settings/${name}`, { value }).catch(() => { /* kept in this browser; the server copy catches up next change */ })
  }, [grid])

  const run = useCallback((p: Promise<unknown>) => p.then(reload), [reload])
  return {
    views: data?.views ?? [],
    error,
    setting,
    putSetting,
    saveView: (name: string, query: string, shared = false) => run(api.post(`${path(grid)}/views`, { name, query, shared })),
    shareView: (id: string, shared: boolean) => run(api.post(`${path(grid)}/views/${id}/share`, { shared })),
    deleteView: (id: string) => run(api.del(`${path(grid)}/views/${id}`)),
  }
}
