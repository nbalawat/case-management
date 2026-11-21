import type { ReactNode } from 'react'
import * as T from '@radix-ui/react-tooltip'

export const TooltipProvider = T.Provider

/** A label shown on hover and focus, used where the sidebar is collapsed to icons. */
export function Tooltip({ label, children, side = 'right', disabled }: { label: ReactNode; children: ReactNode; side?: 'right' | 'bottom'; disabled?: boolean }) {
  if (disabled) return <>{children}</>
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal><T.Content side={side} sideOffset={8} className="z-50 rounded-md bg-[var(--tooltip)] px-2 py-1 text-xs text-white">{label}</T.Content></T.Portal>
    </T.Root>
  )
}
