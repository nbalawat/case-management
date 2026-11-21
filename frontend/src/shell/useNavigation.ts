import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api'
import type { Badges } from './registry'

export type Navigation = { desks: string[]; badges: Badges }

/** The person's desks and menu counts, refreshed as they move between pages so counts follow their work. */
export function useNavigation(personId: string | undefined) {
  const [nav, setNav] = useState<Navigation | null>(null)
  const { pathname } = useLocation()
  useEffect(() => {
    if (!personId) return
    let live = true
    api.get<Navigation>('/navigation').then(n => { if (live) setNav(n) }).catch(() => { if (live) setNav({ desks: [], badges: { my_open: 0, queue_unassigned: 0, alerts_waiting: 0 } }) })
    return () => { live = false }
  }, [personId, pathname])
  return nav
}
