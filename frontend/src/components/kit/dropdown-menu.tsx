import type { ComponentPropsWithoutRef } from 'react'
import * as Menu from '@radix-ui/react-dropdown-menu'
import { Check } from 'lucide-react'
import { cn } from '../../lib/cn'

/** Accessible dropdown menu on Radix: keyboard, focus and dismissal handled by the primitive. */
export const DropdownMenu = Menu.Root
export const DropdownMenuTrigger = Menu.Trigger
export const DropdownMenuGroup = Menu.Group
export const DropdownMenuRadioGroup = Menu.RadioGroup

const surface = 'z-50 min-w-56 rounded-xl border border-line bg-surface p-1.5 text-ink shadow-[0_12px_32px_rgb(11_15_26/0.16)]'
const row = 'relative flex cursor-pointer select-none items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm outline-none ' +
  'data-[highlighted]:bg-brand-soft data-[highlighted]:text-brand data-[disabled]:pointer-events-none data-[disabled]:opacity-50'

export const DropdownMenuContent = ({ className, sideOffset = 6, ...p }: ComponentPropsWithoutRef<typeof Menu.Content>) =>
  <Menu.Portal><Menu.Content sideOffset={sideOffset} className={cn(surface, 'max-h-[75vh] overflow-y-auto', className)} {...p} /></Menu.Portal>

export const DropdownMenuItem = ({ className, ...p }: ComponentPropsWithoutRef<typeof Menu.Item>) =>
  <Menu.Item className={cn(row, className)} {...p} />

export const DropdownMenuRadioItem = ({ className, children, ...p }: ComponentPropsWithoutRef<typeof Menu.RadioItem>) => (
  <Menu.RadioItem className={cn(row, 'pl-8', className)} {...p}>
    <Menu.ItemIndicator className="absolute left-2.5 inline-flex"><Check size={15} /></Menu.ItemIndicator>
    {children}
  </Menu.RadioItem>
)

export const DropdownMenuLabel = ({ className, ...p }: ComponentPropsWithoutRef<typeof Menu.Label>) =>
  <Menu.Label className={cn('px-2.5 pt-2 pb-1 text-xs font-semibold text-muted', className)} {...p} />

export const DropdownMenuSeparator = ({ className, ...p }: ComponentPropsWithoutRef<typeof Menu.Separator>) =>
  <Menu.Separator className={cn('my-1.5 h-px bg-line', className)} {...p} />

export const DropdownMenuCheckboxItem = ({ className, children, ...p }: ComponentPropsWithoutRef<typeof Menu.CheckboxItem>) => (
  <Menu.CheckboxItem className={cn(row, 'pl-8', className)} {...p}>
    <Menu.ItemIndicator className="absolute left-2.5 inline-flex"><Check size={15} /></Menu.ItemIndicator>
    {children}
  </Menu.CheckboxItem>
)
