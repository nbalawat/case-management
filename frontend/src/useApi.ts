import { useCallback, useEffect, useState } from 'react'
import { api } from './api'

/** Fetch on mount and whenever `deps` change; exposes reload for after mutations. */
export function useApi<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    if (!path) return
    api.get<T>(path).then(d => { setData(d); setError(null) }).catch(e => setError(e.message))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps])
  useEffect(load, [load])
  return { data, error, reload: load }
}
