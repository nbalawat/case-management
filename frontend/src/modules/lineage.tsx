import { Route } from 'lucide-react'
import { can } from '../shell/access'
import { page } from '../shell/page'
import type { Module } from '../shell/registry'

const lineage: Module = {
  id: 'lineage', path: '/lineage', label: 'Data lineage', group: 'oversight',
  description: 'Which jobs read and wrote which data, as OpenLineage events', icon: Route,
  page: page(() => import('../pages/Lineage')), visible: can('view_team'), after: 'lake-sql',
  keywords: ['lineage', 'openlineage', 'provenance', 'catalog', 'marquez', 'data flow'],
}

export default lineage
