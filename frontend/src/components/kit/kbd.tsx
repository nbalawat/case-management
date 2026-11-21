import type { ReactNode } from 'react'

export const Kbd = ({ children }: { children: ReactNode }) =>
  <kbd className="rounded border border-line bg-bg-2 px-1.5 font-sans text-[11px] leading-5 text-muted">{children}</kbd>
