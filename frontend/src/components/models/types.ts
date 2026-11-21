export type Status = 'candidate' | 'approved' | 'champion' | 'challenger' | 'retired'

export type ModelRow = {
  id: string; name: string; version: number; algorithm: string; status: Status; trained_by: string; trained: string
  approved_by: string | null; approved: string | null; auc: number | null; pr_auc: number | null; base_rate: number | null
  could_wait_at_95: number | null; rows: number | null; positives: number | null; sources: Record<string, number>
}

export type Registry = {
  name: string
  versions: ModelRow[]
  data: { examples: number; positives: number; loaded_labels: Record<string, number>; min_rows: number; min_each: number }
  can: { train: boolean; approve: boolean; me: string }
}

export type Drift = {
  days: number; alerts: number; min_alerts?: number; enough?: boolean; scored?: number; mean_score?: number | null
  features: { feature: string; label: string; psi: number; state: 'stable' | 'watch' | 'moved' | 'too_few' }[]
}

export type ModelCard = ModelRow & {
  metrics: {
    auc: number; pr_auc: number; brier: number; base_rate: number; validation_auc?: Record<string, number>; other_algorithm?: string; other_auc?: number
    work: { curve: { reviewed: number; found: number }[]; could_wait_at_95: number; could_wait_at_99: number }
  }
  data: Record<string, unknown> & { rows?: number; train_rows?: number; test_rows?: number; from?: string; to?: string; test_from?: string }
  params: Record<string, unknown>
  note: string
  history: { at: string; what: string; by: string; note?: string }[]
  feature_set: { id: string; version: string }
  features: { name: string; label: string }[]
  drift: Drift
  sha256: string
}

export type FeatureSet = {
  id: string; title: string; entity: string; version: string; description: string
  features: { name: string; label: string; source: string; agg: string | null; field: string | null; window_days: number | null; where: Record<string, unknown> | null }[]
}

export const pct = (x: number | null | undefined, digits = 0) => (x == null ? 'n/a' : `${(x * 100).toFixed(digits)}%`)
export const num = (x: number | null | undefined, digits = 2) => (x == null ? 'n/a' : x.toFixed(digits))
/** Server times are UTC, sometimes without a zone. */
export const utc = (iso: string) => new Date(iso + (/([zZ]|[+-]\d\d:\d\d)$/.test(iso) ? '' : 'Z'))
export const day = (iso: string) => utc(iso).toLocaleDateString()

export const STATUS_TONE: Record<Status, string> = { champion: 'good', challenger: 'human', approved: 'neutral', candidate: 'warning', retired: 'neutral' }
