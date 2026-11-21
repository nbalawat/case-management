import { BookOpen } from 'lucide-react'
import { everyone } from '../shell/access'
import { page } from '../shell/page'
import type { Module } from '../shell/registry'

const Knowledge = page(() => import('../pages/Knowledge'))

const knowledge = {
  id: 'knowledge', path: '/knowledge', label: 'Knowledge library', group: 'investigate',
  description: 'Typologies, policies and procedures that Ask and the agents cite', icon: BookOpen, visible: everyone,
  keywords: ['policy', 'procedure', 'typology', 'red flags'], page: Knowledge, after: 'ask',
  children: [{ path: '/knowledge/:doc', page: Knowledge }],
} satisfies Module

export default knowledge
