import type { ShellContext } from './access'
import { GROUPS, HOME_ORDER, MODULES, type GroupId, type Module } from './registry'

/** The menu one person sees: the registry filtered by their context, grouped, empty groups dropped. */
export function menuFor(ctx: ShellContext, modules: Module[] = MODULES): { id: GroupId; label: string; items: Module[] }[] {
  return GROUPS.map(g => ({ ...g, items: modules.filter(m => m.group === g.id && m.visible(ctx)) })).filter(g => g.items.length > 0)
}

/** The path a person lands on. */
export function homeFor(ctx: ShellContext, modules: Module[] = MODULES): string {
  for (const id of HOME_ORDER) {
    const m = modules.find(x => x.id === id)
    if (m && m.visible(ctx)) return m.path
  }
  return '/cases'
}
