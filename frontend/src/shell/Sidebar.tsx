import { NavLink } from 'react-router-dom'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { cn } from '../lib/cn'
import { Tooltip } from '../components/kit/tooltip'
import type { Badges, GroupId, Module } from './registry'

type Group = { id: GroupId; label: string; items: Module[] }

/** The role-shaped menu: groups of modules this person uses, with live counts. Collapses to icons. */
export default function Sidebar({ groups, badges, collapsed, onCollapse, onNavigate, clientName }: {
  groups: Group[]; badges: Badges | null; collapsed: boolean; onCollapse: (c: boolean) => void; onNavigate?: () => void; clientName?: string
}) {
  return (
    <nav aria-label="Main" className={cn('sidebar flex h-full flex-col border-r border-line bg-surface', collapsed ? 'w-[64px]' : 'w-[240px]')}>
      <div className={cn('flex h-14 shrink-0 items-center gap-2.5 border-b border-line', collapsed ? 'justify-center px-0' : 'px-4')}>
        <span className="mark shrink-0" aria-hidden />
        {!collapsed && <div className="min-w-0 leading-tight"><b className="block text-[16px]">CaseForge</b>
          {clientName && <span className="block truncate text-xs text-muted">{clientName}</span>}</div>}
      </div>
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {groups.map(g => (
          <div key={g.id} className={cn('mb-1', g.id === 'admin' && 'mt-2 border-t border-line pt-2')}>
            {!collapsed && <div className="px-2.5 pt-2.5 pb-1 text-xs font-semibold text-muted">{g.label}</div>}
            {collapsed && g.id !== 'work' && g.id !== 'admin' && <div className="mx-3 my-2 h-px bg-line" aria-hidden />}
            <ul className="m-0 list-none p-0">
              {g.items.map(m => {
                const b = badges && m.badge ? m.badge(badges) : null
                const Icon = m.icon
                return (
                  <li key={m.id}>
                    <Tooltip label={m.label} disabled={!collapsed}>
                      <NavLink to={m.path} end={m.path === '/' || m.path === '/overview'} onClick={onNavigate} aria-label={collapsed ? m.label : undefined}
                        className={({ isActive }) => cn('side-link relative flex items-center gap-2.5 rounded-lg py-[7px] text-[13.5px] font-medium text-soft no-underline hover:bg-bg-2 hover:text-ink hover:no-underline',
                          collapsed ? 'justify-center px-0' : 'px-2.5', isActive && 'bg-brand-soft text-brand hover:bg-brand-soft hover:text-brand')}>
                        <Icon size={17} strokeWidth={1.9} className="shrink-0" aria-hidden />
                        {!collapsed && <span className="flex-1 truncate">{m.label}</span>}
                        {b && !collapsed && <span className={cn('rounded-full px-1.5 text-[11.5px] leading-[18px] tabular-nums', b.tone === 'alert' ? 'bg-crit-bg text-crit' : 'bg-chip text-soft')}>{b.value}</span>}
                        {b && collapsed && <span className="absolute top-1 right-2.5 h-1.5 w-1.5 rounded-full bg-brand" aria-hidden />}
                      </NavLink>
                    </Tooltip>
                  </li>)
              })}
            </ul>
          </div>))}
      </div>
      <button type="button" onClick={() => onCollapse(!collapsed)} aria-label={collapsed ? 'Expand menu' : 'Collapse menu'}
        className="collapse-btn m-2 hidden h-9 min-h-0 items-center justify-center gap-2 rounded-lg border-0 bg-transparent text-[13px] text-muted hover:bg-bg-2 hover:text-ink lg:flex">
        {collapsed ? <PanelLeftOpen size={17} /> : <><PanelLeftClose size={17} /><span>Collapse</span></>}
      </button>
    </nav>
  )
}
