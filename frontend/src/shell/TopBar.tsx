import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ChevronRight, Menu, Search } from 'lucide-react'
import { Kbd } from '../components/kit/kbd'
import Notifications from './Notifications'
import { GROUPS, type Module } from './registry'

const isMac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)

/** Where you are, the one box for search and questions, and who you are. */
export default function TopBar({ current, detail, onMenu, onPalette, identity }: {
  current?: Module; detail: boolean; onMenu: () => void; onPalette: () => void; identity: ReactNode
}) {
  const group = current && GROUPS.find(g => g.id === current.group)
  return (
    <header className="topbar sticky top-0 z-30 flex h-14 items-center gap-3 border-b border-line bg-surface/95 px-4 backdrop-blur lg:px-6">
      <button type="button" onClick={onMenu} aria-label="Open menu" className="grid h-9 min-h-0 w-9 place-items-center rounded-lg border-0 bg-transparent p-0 text-soft hover:bg-bg-2 lg:hidden">
        <Menu size={19} />
      </button>
      <div className="crumbs-top hidden min-w-0 items-center gap-1.5 text-[13.5px] sm:flex" aria-label="Breadcrumb">
        {group && <span className="text-muted">{group.label}</span>}
        {group && <ChevronRight size={14} className="text-muted" aria-hidden />}
        {current && (detail ? <Link to={current.path} className="font-medium text-soft">{current.label}</Link> : <b className="font-semibold">{current.label}</b>)}
      </div>
      <button type="button" onClick={onPalette} aria-label="Search, go to or ask"
        className="palette-trigger mx-auto flex h-9 min-h-0 w-full max-w-[460px] items-center gap-2.5 rounded-lg border border-line bg-bg px-3 text-left text-[13.5px] font-normal text-muted hover:border-line-strong">
        <Search size={15} aria-hidden /><span className="flex-1 truncate">Search, go to, or ask anything</span><Kbd>{isMac ? '⌘K' : 'Ctrl K'}</Kbd>
      </button>
      <div className="ml-auto flex shrink-0 items-center"><Notifications />{identity}</div>
    </header>
  )
}
