import {
  ArrowUp, BadgeCheck, Banknote, Bell, Bot, ClipboardList, DoorOpen, FilePen, Flag, Gauge, GitFork, Hourglass, MessageSquare, Play,
  Radio, Send, Shield, ShieldCheck, Zap, type LucideIcon,
} from 'lucide-react'

/** Block and action icons by the names the server's catalog uses. Unknown names fall back to a neutral icon. */
const ICONS: Record<string, LucideIcon> = {
  play: Play, 'clipboard-list': ClipboardList, hourglass: Hourglass, 'badge-check': BadgeCheck, 'git-fork': GitFork, flag: Flag,
  'file-pen': FilePen, 'message-square': MessageSquare, banknote: Banknote, gauge: Gauge, shield: Shield, send: Send, 'door-open': DoorOpen,
  bell: Bell, 'arrow-up': ArrowUp, radio: Radio, 'shield-search': ShieldCheck, bot: Bot, zap: Zap,
}
export const iconFor = (name: string | undefined): LucideIcon => (name && ICONS[name]) || Zap

/** Each kind of block has one colour on the canvas and in the palette, so the shape of a workflow reads at a glance. */
export const TONE: Record<string, string> = { start: 'start', stage: 'stage', wait: 'wait', approval: 'approval', decision: 'decision', outcome: 'outcome' }
export const toneOf = (type: string, kind?: string) => TONE[type] ?? TONE[kind ?? ''] ?? 'stage'
