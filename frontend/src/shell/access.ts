/**
 * What the shell knows about a person when it decides what to show them. Pure data, so the rules that shape
 * each person's menu can be tested without rendering anything. The server still enforces every permission:
 * hiding a menu item is about focus, never about security.
 */
export type ShellContext = {
  permissions: ReadonlySet<string>
  readOnly: boolean
  /** Domains of the case types this person works (from GET /api/navigation), e.g. KYC, Sanctions. */
  desks: ReadonlySet<string>
}

export type Rule = (c: ShellContext) => boolean

export const can = (...perms: string[]): Rule => c => perms.some(p => c.permissions.has(p))
export const desk = (domain: string): Rule => c => c.desks.has(domain)
export const readOnly: Rule = c => c.readOnly
export const everyone: Rule = () => true
export const anyOf = (...rules: Rule[]): Rule => c => rules.some(r => r(c))

export const shellContext = (permissions: string[], readOnlyFlag: boolean, desks: string[]): ShellContext =>
  ({ permissions: new Set(permissions), readOnly: readOnlyFlag, desks: new Set(desks) })
