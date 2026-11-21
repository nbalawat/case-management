import { lazy, type ComponentType } from 'react'

/** A lazily loaded page. Its own file so modules in src/modules/ can use it without importing the registry. */
export const page = (load: () => Promise<{ default: ComponentType }>) => lazy(load)
