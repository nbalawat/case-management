export type Code = { code: string; label: string } & Record<string, unknown>
export type Factor = { code: string; label: string; points: number; applies: boolean; evidence: string }
export type Override = { band: string; reason_code: string; reason: string; note: string; by: string; at: string; status: string; decided_by: string | null; decided_at: string | null; decision_note: string }
export type Assessment = { id: string; at: string; by: string; score: number; band: string; final_band: string; factors: Factor[]; override: Override | null }
export type Risk = {
  rating: string; assessment: Assessment | null; bands: { code: string; label: string; from_score: number; review_months: number }[]; review_months: number | null
  reasons: Code[]; stale: boolean; can_assess: boolean; can_override: boolean; can_decide: boolean
  history: { id: string; at: string; by: string; score: number; band: string; final_band: string; override: string | null }[]
}
export type OwnNode = { key: string; name: string; kind: string; country: string; pep: boolean; verified: boolean }
export type OwnLink = { owner: string; owned: string; percent: number; control: boolean }
export type OwnerRow = OwnNode & { effective: number; control: boolean; layers: number; beneficial_owner: boolean }
export type Analysis = { owners: OwnerRow[]; beneficial_owners: OwnerRow[]; unidentified: number; layers: number; threshold: number; flags: { code: string; text: string }[] }
export type Ownership = {
  structure: null | { id: string; version: number; nodes: OwnNode[]; links: OwnLink[]; source: string; note: string; recorded_by: string; recorded_at: string }
  analysis: Analysis | null; kinds: string[]; can_record: boolean
  history: { version: number; recorded_by: string; recorded_at: string; source: string; note: string; beneficial_owners: string[] }[]
}
export type Doc = {
  id: string; party_id: string; case_id: string | null; kind: string; kind_label: string; title: string; filename: string; media_type: string; size: number; sha256: string
  status: string; expires: string | null; expired: boolean; uploaded_by: string; uploaded_at: string; verified_by: string | null; verified_at: string | null
  redacted: boolean; redactions: { kind: string; count: number }[]; redactable: boolean; custody_intact: boolean
  custody: { at: string; by: string; action: string; detail: string }[]
}
export type EvidenceView = { documents: Doc[]; hidden: number; types: (Code & { expires?: string })[]; patterns: { id: string; label: string }[]; can_change: boolean; max_bytes: number }
export type Trigger = { id: string; kind: string; label: string; detail: string; at: string; outcome: string; case_id: string | null }
export type Kyc = { risk: Risk; ownership: Ownership; evidence: EvidenceView; triggers: Trigger[] }
export const BAND: Record<string, string> = { high: 'critical', medium: 'warning', low: 'good' }
export const OUTCOME: Record<string, string> = { opened_review: 'Opened a review', joined_review: 'Added to the open review', noted: 'Noted' }
