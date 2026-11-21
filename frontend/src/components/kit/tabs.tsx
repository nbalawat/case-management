import type { ComponentPropsWithoutRef } from 'react'
import * as T from '@radix-ui/react-tabs'
import { cn } from '../../lib/cn'

/** Accessible tabs on Radix: arrow keys move between tabs, each panel is labelled by its tab. */
export const Tabs = T.Root
export const TabsContent = T.Content

export const TabsList = ({ className, ...p }: ComponentPropsWithoutRef<typeof T.List>) =>
  <T.List className={cn('tabs-list flex gap-1 overflow-x-auto border-b border-line', className)} {...p} />

export const TabsTrigger = ({ className, ...p }: ComponentPropsWithoutRef<typeof T.Trigger>) =>
  <T.Trigger className={cn('tabs-trigger -mb-px inline-flex min-h-0 shrink-0 cursor-pointer items-center gap-1.5 rounded-none border-0 border-b-2 border-transparent bg-transparent px-3 py-2.5 text-[13.5px] font-medium text-soft',
    'hover:text-ink data-[state=active]:border-brand data-[state=active]:text-brand focus-visible:outline-2 focus-visible:outline-brand', className)} {...p} />
