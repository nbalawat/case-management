import type { Hold } from './HoldsCard'

/** What a hold is on, in words. */
export const holdWhat = (h: Hold) => `${h.target_label}${h.target_id ? ` ${h.target_id}` : h.target === 'payment' ? '' : ' (all accounts)'}`
