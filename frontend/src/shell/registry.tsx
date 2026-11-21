/**
 * The module registry: the single list of everything the app can show. Each module declares its route, how it
 * appears in the menu, who it is for and what count it carries. The sidebar, the command palette, the page
 * title and the routes are all built from this list.
 *
 * Adding a page: put one file in `src/modules/` that default-exports a `Module` (or an array of them). It is
 * picked up automatically, so parallel features never edit this file or the same line. Use `after` to place it
 * in its group after another module's id; without it, it goes at the end of its group. Example:
 *
 *   // src/modules/workflow-designer.tsx
 *   import { PencilRuler } from 'lucide-react'
 *   import { can } from '../shell/access'
 *   import { page } from '../shell/page'
 *   import type { Module } from '../shell/registry'
 *   export default { id: 'workflow-designer', path: '/workflows/designer', label: 'Workflow designer', group: 'admin',
 *     description: '...', icon: PencilRuler, page: page(() => import('../pages/WorkflowDesignerHome')),
 *     visible: can('publish_workflow'), after: 'forms' } satisfies Module
 */
import type { ComponentType, LazyExoticComponent } from 'react'
import {
  BarChart3, Bot, Boxes, ClipboardList, CreditCard, Database, FileText, FolderOpen, IdCard, Inbox, LayoutDashboard, ListChecks, ListFilter, Network, Plug,
  Radar, ScrollText, ShieldAlert, ShieldCheck, Siren, Sparkles, TableProperties, Users, Workflow, type LucideIcon,
} from 'lucide-react'
import { anyOf, can, desk, everyone, readOnly, type Rule } from './access'
import { page } from './page'

export { page }

export type GroupId = 'work' | 'desks' | 'investigate' | 'oversight' | 'admin'
export const GROUPS: { id: GroupId; label: string }[] = [
  { id: 'work', label: 'Work' },
  { id: 'desks', label: 'Desks' },
  { id: 'investigate', label: 'Investigate' },
  { id: 'oversight', label: 'Oversight' },
  { id: 'admin', label: 'Administration' },
]

/** Counts returned by GET /api/navigation. */
export type Badges = { my_open: number; queue_unassigned: number; alerts_waiting: number }
export type Badge = { value: number; tone?: 'alert' } | null

type Page = LazyExoticComponent<ComponentType>
export type Module = {
  id: string
  /** Where the module lives; also what the menu links to. */
  path: string
  label: string
  /** One line for the command palette. */
  description: string
  icon: LucideIcon
  group: GroupId
  page: Page
  /** Who sees it in the menu. Everyone can still open the route; the server decides what they may do there. */
  visible: Rule
  /** Further routes that belong to this module (detail pages), so the menu and title stay on it. */
  children?: { path: string; page: Page }[]
  badge?: (b: Badges) => Badge
  keywords?: string[]
  /** For a module in `src/modules/`: the id of the module it follows in its group. */
  after?: string
}

const manager = can('view_team')

const CORE: Module[] = [
  // Work
  { id: 'my-work', path: '/', label: 'My work', description: 'Your cases, then unassigned work in your queues', icon: Inbox, group: 'work',
    page: page(() => import('../pages/MyWork')), visible: can('work'), keywords: ['inbox', 'queue', 'claim'],
    badge: b => (b.my_open ? { value: b.my_open } : null) },
  { id: 'cases', path: '/cases', label: 'Cases', description: 'Every case you can see, with bulk actions', icon: FolderOpen, group: 'work',
    page: page(() => import('../pages/Cases')), visible: everyone, children: [{ path: '/cases/:id', page: page(() => import('../pages/CaseWorkspace')) }] },
  { id: 'alerts', path: '/alerts', label: 'Alert intake', description: 'Weak alerts waiting instead of opening a case', icon: Siren, group: 'work',
    page: page(() => import('../pages/Alerts')), visible: anyOf(can('work'), manager), keywords: ['hibernated', 'wake'],
    badge: b => (b.alerts_waiting ? { value: b.alerts_waiting } : null) },

  // Desks: shown to the people who work that kind of case
  { id: 'kyc', path: '/kyc', label: 'KYC', description: 'Risk ratings, ownership, documents and periodic reviews', icon: IdCard, group: 'desks',
    page: page(() => import('../pages/Kyc')), visible: desk('KYC'), keywords: ['due diligence', 'onboarding', 'risk rating', 'ubo'] },
  { id: 'sanctions', path: '/sanctions', label: 'Sanctions', description: 'Held payments, false-match exemptions and law enforcement requests', icon: ShieldAlert, group: 'desks',
    page: page(() => import('../pages/Sanctions')), visible: desk('Sanctions'), keywords: ['screening', 'hold', '314a', 'ofac'] },
  { id: 'fraud', path: '/fraud', label: 'Fraud', description: 'Claims, disputes and refunds', icon: CreditCard, group: 'desks',
    page: page(() => import('../pages/Fraud')), visible: desk('Fraud'), keywords: ['dispute', 'chargeback', 'refund'] },

  // Investigate
  { id: 'ask', path: '/ask', label: 'Ask', description: 'Ask any question about your caseload in plain language', icon: Sparkles, group: 'investigate',
    page: page(() => import('../pages/Ask')), visible: everyone, keywords: ['question', 'ai', 'agent'] },
  { id: 'entities', path: '/entities', label: 'Entities', description: 'Customers, counterparties and possible duplicates', icon: Network, group: 'investigate',
    page: page(() => import('../pages/Entities')), visible: everyone, keywords: ['customer', 'counterparty', 'resolution', 'network'],
    children: [{ path: '/customers/:id', page: page(() => import('../pages/Customer')) }, { path: '/counterparties/:id', page: page(() => import('../pages/Counterparty')) }] },
  { id: 'filings', path: '/filings', label: 'Regulatory reports', description: 'SARs, CTRs and blocking reports: prepare, send, amend', icon: FileText, group: 'investigate',
    page: page(() => import('../pages/Filings')), visible: anyOf(can('submit_filings'), manager), keywords: ['sar', 'ctr', 'filing', 'regulator'],
    children: [{ path: '/filings/:id', page: page(() => import('../pages/Filing')) }] },

  // Oversight
  { id: 'overview', path: '/overview', label: 'Dashboard', description: 'The programme today: volumes, deadlines, agent agreement', icon: LayoutDashboard, group: 'oversight',
    page: page(() => import('../pages/Overview')), visible: anyOf(manager, readOnly), keywords: ['overview', 'kpi'] },
  { id: 'mi', path: '/overview/report', label: 'Management reports', description: 'Month-by-month management information, with export', icon: BarChart3, group: 'oversight',
    page: page(() => import('../pages/Mi')), visible: manager, keywords: ['mi', 'board pack', 'trend'] },
  { id: 'lake-sql', path: '/lake', label: 'Lake SQL', description: 'Read-only SQL over the full history in the lake, with time travel', icon: Database, group: 'oversight',
    page: page(() => import('../pages/LakeSql')), visible: manager, keywords: ['sql', 'iceberg', 'duckdb', 'analytics', 'history', 'query'] },
  { id: 'team', path: '/team', label: 'Team and queues', description: 'Workload by person, backlogs, breaches and QA', icon: Users, group: 'oversight',
    page: page(() => import('../pages/Team')), visible: manager, keywords: ['workload', 'reassign', 'qa'] },
  { id: 'agents', path: '/agents', label: 'Agents', description: 'What each agent does, its autonomy and its record', icon: Bot, group: 'oversight',
    page: page(() => import('../pages/Agents')), visible: anyOf(can('manage_agents'), manager), keywords: ['autonomy', 'kill switch', 'evaluation'] },
  { id: 'governance', path: '/governance', label: 'Governance', description: 'Change register, access recertification and audit search', icon: ScrollText, group: 'oversight',
    page: page(() => import('../pages/Governance')), visible: manager, keywords: ['audit', 'recertification', 'change register'] },

  // Administration
  { id: 'workflows', path: '/workflows', label: 'Workflows', description: 'Case types as diagrams; edit, check and publish', icon: Workflow, group: 'admin',
    page: page(() => import('../pages/Workflows')), visible: anyOf(can('publish_workflow'), manager) },
  { id: 'decisions', path: '/decisions', label: 'Decision tables', description: 'Routing, priority and risk rules as DMN tables; check, replay and publish', icon: TableProperties, group: 'admin',
    page: page(() => import('../pages/Decisions')), visible: anyOf(can('publish_workflow'), manager), keywords: ['dmn', 'rules', 'routing', 'priority', 'risk band'] },
  { id: 'forms', path: '/forms', label: 'Forms', description: 'Build the questionnaires and decision records a case asks for at a stage', icon: ClipboardList, group: 'admin',
    page: page(() => import('../pages/Forms')), visible: anyOf(can('publish_workflow'), manager), keywords: ['form builder', 'questionnaire', 'edd', 'json schema'] },
  { id: 'detection', path: '/detection', label: 'Detection tuning', description: 'Scenario settings, backtests and lookback projects', icon: Radar, group: 'admin',
    page: page(() => import('../pages/Detection')), visible: can('tune_detection', 'approve_tuning'), keywords: ['scenario', 'threshold', 'backtest', 'lookback'] },
  { id: 'screening-lists', path: '/screening-lists', label: 'Screening lists', description: 'Load sanctions list versions and edit the client\'s own list', icon: ListFilter, group: 'admin',
    page: page(() => import('../pages/ScreeningLists')), visible: can('manage_lists'), keywords: ['ofac', 'sanctions list', 'watchlist', 'load list'] },
  { id: 'lists', path: '/reference', label: 'Code lists', description: 'Closing reasons, hibernation rules and other reference codes', icon: ListChecks, group: 'admin',
    page: page(() => import('../pages/Reference')), visible: anyOf(can('publish_workflow', 'manage_access'), manager), keywords: ['reference', 'codes'] },
  { id: 'access', path: '/access', label: 'Access', description: 'Roles, permissions, people and queues for this client', icon: ShieldCheck, group: 'admin',
    page: page(() => import('../pages/Access')), visible: anyOf(can('manage_access'), manager), keywords: ['roles', 'permissions', 'rbac'] },
  { id: 'data', path: '/data', label: 'Data pipelines', description: 'Sources in object storage, runs and data quality', icon: Boxes, group: 'admin',
    page: page(() => import('../pages/Data')), visible: can('load_data'), keywords: ['ingest', 's3', 'adls', 'gcs', 'feed'],
    children: [{ path: '/onboarding', page: page(() => import('../pages/Onboarding')) }] },
  { id: 'platform', path: '/platform', label: 'Integrations', description: 'Connected systems, events and scale', icon: Plug, group: 'admin',
    page: page(() => import('../pages/Platform')), visible: can('manage_access', 'load_data'), keywords: ['api', 'kafka', 'webhook'] },
]

/** Modules defined one per file in src/modules/, found at build time. */
const DISCOVERED: Module[] = Object.entries(import.meta.glob<{ default: Module | Module[] }>('../modules/*.tsx', { eager: true }))
  .sort(([a], [b]) => a.localeCompare(b))
  .flatMap(([, m]) => [m.default].flat())

/** Put each discovered module after the one it names, or at the end of its group. */
export function place(core: Module[], extra: Module[]): Module[] {
  const out = [...core]
  for (const m of extra) {
    let at = m.after ? out.findIndex(x => x.id === m.after) : -1
    if (at < 0) at = out.map(x => x.group).lastIndexOf(m.group)
    out.splice(at < 0 ? out.length : at + 1, 0, m)
  }
  return out
}

export const MODULES: Module[] = place(CORE, DISCOVERED)

/** The module a path belongs to, matching detail routes to their parent. */
export function moduleFor(pathname: string): Module | undefined {
  const match = (pattern: string) => {
    const re = new RegExp(`^${pattern.replace(/:[^/]+/g, '[^/]+')}/?$`)
    return re.test(pathname)
  }
  return MODULES.find(m => match(m.path) || m.children?.some(c => match(c.path)))
}

/** Where a person starts: their own work if they work cases, otherwise the first oversight or admin page they have. */
export const HOME_ORDER = ['my-work', 'overview', 'data', 'workflows', 'cases']
