import { Suspense, useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import type { Persona } from '../api'
import { Loading } from '../components/ui'
import { TooltipProvider } from '../components/kit/tooltip'
import { shellContext } from './access'
import CommandPalette from './CommandPalette'
import { usePaletteShortcut } from './shortcuts'
import DemoBanner from './DemoBanner'
import { homeFor, menuFor } from './menu'
import { MODULES, moduleFor } from './registry'
import Sidebar from './Sidebar'
import { isDark, useTheme } from './theme'
import TopBar from './TopBar'
import { useNavigation } from './useNavigation'
import { usePeople } from './people'

const COLLAPSE_KEY = 'sidebar-collapsed'

/** The frame every page sits in: role-shaped sidebar, top bar with the command palette, and the routes. */
export default function AppShell({ clientName, people, sso, sample, identity, onPerson }: {
  clientName: string; people: Persona[]; sso: boolean; sample: boolean
  identity: (theme: ReturnType<typeof useTheme>) => ReactNode; onPerson?: (id: string) => void
}) {
  const { persona } = usePeople()
  const navigation = useNavigation(persona?.id)
  const ctx = useMemo(() => shellContext(persona?.permissions ?? [], !!persona?.read_only, navigation?.desks ?? []), [persona, navigation])
  const groups = useMemo(() => menuFor(ctx), [ctx])
  const visible = useMemo(() => groups.flatMap(g => g.items), [groups])
  const { pathname } = useLocation()
  const current = moduleFor(pathname)
  const theme = useTheme()
  const [collapsed, setCollapsed] = useState(() => { try { return localStorage.getItem(COLLAPSE_KEY) === '1' } catch { return false } })
  const collapse = (c: boolean) => { setCollapsed(c); try { localStorage.setItem(COLLAPSE_KEY, c ? '1' : '0') } catch { /* ignore */ } }
  const [drawer, setDrawer] = useState(false)
  const [palette, setPalette] = useState(false)
  const openPalette = useCallback(() => setPalette(true), [])
  usePaletteShortcut(openPalette)
  useEffect(() => { document.title = current ? `${current.label} · CaseForge` : 'CaseForge' }, [current])
  const [choice, setChoice] = theme
  const dark = isDark(choice)
  const home = navigation ? homeFor(ctx) : null

  return (
    <TooltipProvider delayDuration={200}>
      <div className="shell flex min-h-screen bg-bg">
        <a href="#content" className="skip-link">Skip to content</a>
        <div className="sticky top-0 hidden h-screen shrink-0 lg:block">
          <Sidebar groups={groups} badges={navigation?.badges ?? null} collapsed={collapsed} onCollapse={collapse} clientName={clientName} />
        </div>
        {drawer && (
          <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Menu">
            <div className="absolute inset-0 bg-[rgb(11_15_26/0.45)]" onClick={() => setDrawer(false)} />
            <div className="absolute inset-y-0 left-0 shadow-xl">
              <Sidebar groups={groups} badges={navigation?.badges ?? null} collapsed={false} onCollapse={() => setDrawer(false)} onNavigate={() => setDrawer(false)} clientName={clientName} />
            </div>
          </div>)}
        <div className="flex min-w-0 flex-1 flex-col">
          <TopBar current={current} detail={!!current && current.path !== pathname && pathname !== '/'} onMenu={() => setDrawer(true)} onPalette={openPalette} identity={identity(theme)} />
          {sample && <DemoBanner client={clientName} sso={sso} />}
          <main id="content" key={persona?.id}>
            <Suspense fallback={<Loading />}>
              <Routes>
                {MODULES.flatMap(m => [
                  <Route key={m.id} path={m.path} element={m.id === 'my-work'
                    ? (ctx.permissions.has('work') ? <m.page /> : home ? <Navigate to={home} replace /> : <Loading />)
                    : <m.page />} />,
                  ...(m.children ?? []).map(c => <Route key={c.path} path={c.path} element={<c.page />} />),
                ])}
                <Route path="*" element={<div className="card"><h2>Nothing here</h2><p className="muted">This address does not match a page. Use the menu or press / to search.</p></div>} />
              </Routes>
            </Suspense>
          </main>
        </div>
        <CommandPalette open={palette} onOpenChange={setPalette} modules={visible} people={sso ? undefined : people} onPerson={onPerson}
          dark={dark} onToggleTheme={() => setChoice(dark ? 'light' : 'dark')} />
      </div>
    </TooltipProvider>
  )
}
