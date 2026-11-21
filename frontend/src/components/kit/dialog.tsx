import type { ComponentPropsWithoutRef } from 'react'
import * as D from '@radix-ui/react-dialog'
import { cn } from '../../lib/cn'

/** Modal dialog on Radix: traps focus, closes on Escape, restores focus on close. */
export const Dialog = D.Root
export const DialogTitle = D.Title
export const DialogDescription = D.Description

export const DialogContent = ({ className, children, ...p }: ComponentPropsWithoutRef<typeof D.Content>) => (
  <D.Portal>
    <D.Overlay className="fixed inset-0 z-50 bg-[rgb(11_15_26/0.45)]" />
    <D.Content className={cn('fixed left-1/2 top-[12vh] z-50 w-[min(640px,94vw)] -translate-x-1/2 rounded-2xl border border-line bg-surface text-ink',
      'shadow-[0_24px_64px_rgb(11_15_26/0.28)] outline-none', className)} {...p}>{children}</D.Content>
  </D.Portal>
)

/** A panel that slides in from the right: quick views and details beside a list, without leaving it. */
export const SheetContent = ({ className, children, ...p }: ComponentPropsWithoutRef<typeof D.Content>) => (
  <D.Portal>
    <D.Overlay className="fixed inset-0 z-50 bg-[rgb(11_15_26/0.30)]" />
    <D.Content className={cn('sheet fixed inset-y-0 right-0 z-50 flex w-[min(560px,100vw)] flex-col border-l border-line bg-surface text-ink',
      'shadow-[0_0_48px_rgb(11_15_26/0.25)] outline-none', className)} {...p}>{children}</D.Content>
  </D.Portal>
)
export const DialogClose = D.Close
