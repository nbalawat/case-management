import type { Catalog, FieldSpec, Option } from '../types'

/** The options a field offers: its own list, or one of the client's reference lists named by the catalog. */
export function optionsFor(cat: Catalog, f: FieldSpec): Option[] {
  if (Array.isArray(f.options)) return f.options
  const list = (cat.reference as unknown as Record<string, Option[]>)[f.options]
  return list ?? []
}
