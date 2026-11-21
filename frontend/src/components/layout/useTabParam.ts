import { useSearchParams } from 'react-router-dom'

/** The tab named in the address (`?tab=`), or the first one; switching replaces history so Back leaves the page. */
export function useTabParam(ids: readonly string[], fallback = ids[0]) {
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab')
  const tab = raw && ids.includes(raw) ? raw : fallback
  const setTab = (t: string) => setParams(p => {
    const next = new URLSearchParams(p)
    if (t === fallback) next.delete('tab'); else next.set('tab', t)
    return next
  }, { replace: true })
  return [tab, setTab] as const
}
