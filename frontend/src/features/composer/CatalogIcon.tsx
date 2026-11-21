import { createElement } from 'react'
import { iconFor } from './icons'

/** A catalog icon by name, as a component, so callers never create components while rendering. */
export function CatalogIcon({ name, size = 15, className }: { name: string | undefined; size?: number; className?: string }) {
  return createElement(iconFor(name), { size, className, 'aria-hidden': true })
}
