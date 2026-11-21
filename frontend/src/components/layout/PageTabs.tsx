import type { ReactNode } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../kit/tabs'
import { cn } from '../../lib/cn'
import { useTabParam } from './useTabParam'

export type PageTab = {
  id: string
  label: string
  /** Shown beside the label when above zero. */
  count?: number
  /** Marks the count as needing attention (overdue, waiting on someone). */
  alert?: boolean
  /** Leave a tab out for people it is not for. */
  hidden?: boolean
  content: ReactNode
}

export const TabCount = ({ n, alert }: { n?: number; alert?: boolean }) =>
  n ? <span className={cn('tab-count', alert && 'warn')}>{n}</span> : null

/**
 * A page split into tabs kept in the address, so a link or a dashboard can open the right one. Only the open tab
 * is rendered.
 */
export default function PageTabs({ tabs, label, className }: { tabs: PageTab[]; label: string; className?: string }) {
  const shown = tabs.filter(t => !t.hidden)
  const [tab, setTab] = useTabParam(shown.map(t => t.id))
  return (
    <Tabs value={tab} onValueChange={setTab} className={cn('page-tabs', className)}>
      <TabsList aria-label={label}>
        {shown.map(t => <TabsTrigger key={t.id} value={t.id}>{t.label}<TabCount n={t.count} alert={t.alert} /></TabsTrigger>)}
      </TabsList>
      {shown.map(t => <TabsContent key={t.id} value={t.id} className="ws-stack">{t.content}</TabsContent>)}
    </Tabs>
  )
}
