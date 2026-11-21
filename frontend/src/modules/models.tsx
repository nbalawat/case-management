import { BrainCircuit } from 'lucide-react'
import { can } from '../shell/access'
import { page } from '../shell/page'
import type { Module } from '../shell/registry'

export default {
  id: 'models', path: '/models', label: 'Models', group: 'oversight',
  description: 'Models that rank alerts, their test results and approval, and the feature store',
  icon: BrainCircuit, page: page(() => import('../pages/Models')), visible: can('view_team', 'tune_detection', 'approve_tuning'),
  after: 'lineage', keywords: ['machine learning', 'ml', 'model', 'feature store', 'champion', 'challenger', 'drift', 'scoring'],
} satisfies Module
