import type { ComponentPropsWithoutRef } from 'react'
import { Command as C } from 'cmdk'
import { Search } from 'lucide-react'
import { cn } from '../../lib/cn'

/** Command list on cmdk: fuzzy filtering, arrow keys and Enter. Styled to the product tokens. */
export const Command = ({ className, ...p }: ComponentPropsWithoutRef<typeof C>) =>
  <C className={cn('flex flex-col overflow-hidden rounded-2xl', className)} {...p} />

export const CommandInput = ({ className, ...p }: ComponentPropsWithoutRef<typeof C.Input>) => (
  <div className="flex items-center gap-2.5 border-b border-line px-4">
    <Search size={17} className="shrink-0 text-muted" aria-hidden />
    <C.Input className={cn('h-13 w-full border-0 bg-transparent text-[15px] text-ink outline-none placeholder:text-muted', className)} {...p} />
  </div>
)

export const CommandList = ({ className, ...p }: ComponentPropsWithoutRef<typeof C.List>) =>
  <C.List className={cn('max-h-[min(60vh,460px)] overflow-y-auto p-1.5', className)} {...p} />

export const CommandEmpty = (p: ComponentPropsWithoutRef<typeof C.Empty>) =>
  <C.Empty className="px-3 py-6 text-center text-sm text-muted" {...p} />

export const CommandGroup = ({ className, ...p }: ComponentPropsWithoutRef<typeof C.Group>) =>
  <C.Group className={cn('[&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:pt-2.5 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:text-xs',
    '[&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:text-muted', className)} {...p} />

export const CommandItem = ({ className, ...p }: ComponentPropsWithoutRef<typeof C.Item>) =>
  <C.Item className={cn('flex cursor-pointer items-center gap-3 rounded-lg px-2.5 py-2 text-sm text-ink outline-none',
    'data-[selected=true]:bg-brand-soft data-[selected=true]:text-brand', className)} {...p} />
