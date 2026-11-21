import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import type { SortState } from './types'

/**
 * A list's filters, sort and page live in the address, so a view can be bookmarked, shared, and survives Back.
 * Multi-valued filters repeat their key (?priority=high&priority=critical), as the API expects.
 */
export function useListParams(defaults: Record<string, string>) {
  const [params, setParams] = useSearchParams()
  const get = useCallback((k: string) => params.get(k) ?? defaults[k] ?? '', [params, defaults])
  const getAll = useCallback((k: string) => params.getAll(k), [params])
  const set = useCallback((changes: Record<string, string | string[] | number | null>, resetPage = true) => {
    setParams(prev => {
      const next = new URLSearchParams(prev)
      for (const [k, v] of Object.entries(changes)) {
        next.delete(k)
        if (v === null || v === '' || (Array.isArray(v) && v.length === 0)) continue
        if (Array.isArray(v)) v.forEach(x => next.append(k, x))
        else if (String(v) !== defaults[k]) next.set(k, String(v))
      }
      if (resetPage && !('page' in changes)) next.delete('page')
      return next
    }, { replace: true })
  }, [setParams, defaults])
  const sort = useMemo<SortState>(() => fromSortParam(get('sort')), [get])
  /** The query as the API wants it: defaults filled in. */
  const apiQuery = useMemo(() => {
    const q = new URLSearchParams(params)
    for (const [k, v] of Object.entries(defaults)) if (!q.has(k) && v) q.set(k, v)
    return q.toString()
  }, [params, defaults])
  return { get, getAll, set, sort, apiQuery, query: params.toString(), replaceAll: (q: string) => setParams(new URLSearchParams(q), { replace: true }) }
}

export const fromSortParam = (raw: string): SortState =>
  raw ? raw.split(',').filter(Boolean).map(k => ({ id: k.replace(/^-/, ''), desc: k.startsWith('-') })) : []
export const toSortParam = (s: SortState) => s.map(x => (x.desc ? `-${x.id}` : x.id)).join(',')
