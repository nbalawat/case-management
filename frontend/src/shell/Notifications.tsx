import { Bell } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { api, fmtDateTime } from '../api'
import { useApi } from '../useApi'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '../components/kit/dropdown-menu'
import { cn } from '../lib/cn'

type Item = { id: string; at: string; kind: string; title: string; body: string; case_id: string | null; read: boolean }
type Inbox = { unread: number; items: Item[] }

/** The bell: what happened to my cases while I was elsewhere. Opening an item marks it read and goes to its case. */
export default function Notifications() {
  const { data, reload } = useApi<Inbox>('/notifications')
  const nav = useNavigate()
  const read = (ids?: string[]) => api.post('/notifications/read', ids ? { ids } : {}).then(reload).catch(() => { /* tried again next time */ })
  const unread = data?.unread ?? 0
  return (
    <DropdownMenu onOpenChange={open => open && reload()}>
      <DropdownMenuTrigger asChild>
        <button type="button" aria-label={unread ? `Notifications, ${unread} unread` : 'Notifications'}
          className="relative mr-2 grid h-9 min-h-0 w-9 place-items-center rounded-lg border-0 bg-transparent p-0 text-soft hover:bg-bg-2">
          <Bell size={18} aria-hidden />
          {unread > 0 && <span className="absolute -right-0.5 -top-0.5 min-w-[18px] rounded-full bg-crit px-1 text-center text-[11px] font-semibold leading-[18px] text-white">
            {unread > 99 ? '99+' : unread}</span>}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[360px]">
        <div className="flex items-center justify-between">
          <DropdownMenuLabel>Notifications</DropdownMenuLabel>
          {unread > 0 && <button type="button" className="min-h-0 border-0 bg-transparent px-2 py-1 text-xs text-brand" onClick={() => read()}>Mark all read</button>}
        </div>
        <DropdownMenuSeparator />
        {data && data.items.length === 0 && <p className="px-2 py-3 text-sm text-muted">Nothing yet. You will hear here when a case is assigned to you, a deadline passes, or a request is answered.</p>}
        <div className="max-h-[420px] overflow-y-auto">
          {data?.items.map(n => (
            <DropdownMenuItem key={n.id} onSelect={() => { if (!n.read) read([n.id]); if (n.case_id) nav(`/cases/${n.case_id}`) }}
              className={cn('flex-col items-start gap-0.5', !n.read && 'bg-brand/5')}>
              <span className={cn('text-[13.5px]', !n.read && 'font-semibold')}>{n.title}</span>
              {n.body && <span className="text-xs text-soft">{n.body}</span>}
              <span className="text-[11px] text-muted">{fmtDateTime(n.at)}</span>
            </DropdownMenuItem>))}
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
