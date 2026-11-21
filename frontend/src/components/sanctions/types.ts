export type Entry = { id: string; ref: string; name: string; aliases: string[]; kind: string; countries: string[]; programs: string[]; added_version: number; removed_version: number | null }
export type List = {
  code: string; name: string; kind: string; source: string; version: number; active: boolean; updated_by: string; updated_at: string; entries: number; removed: number
  history: { version: number; at: string; by: string; added: number; changed: number; removed: number; note: string }[]; rows: Entry[]; more: number
}
export type Rescreen = { customers_screened: number; customer_hits: number; payment_hits: number; cases: string[] }
export const rescreened = (r: Rescreen | null) => (r ? ` Rescreened ${r.customers_screened} customers: ${r.customer_hits} customer and ${r.payment_hits} payment hits, ${r.cases.length} case${r.cases.length === 1 ? '' : 's'} opened or added to.` : '')
