import { api } from '../../api'
import type { Catalog, Check, Design, Draft, DraftSummary, Sample, Template, Trial } from './types'

/** Every call the designer makes, in one place. */
export const composerApi = {
  catalog: () => api.get<Catalog>('/composer/catalog'),
  templates: () => api.get<Template[]>('/composer/templates'),
  drafts: (archived = false) => api.get<DraftSummary[]>(`/composer/drafts${archived ? '?archived=true' : ''}`),
  draft: (id: string) => api.get<Draft>(`/composer/drafts/${id}`),
  create: (body: { template?: string; design?: Design; origin?: string }) => api.post<Draft>('/composer/drafts', body),
  save: (id: string, design: Design, revision: number) => api.put<DraftSummary>(`/composer/drafts/${id}`, { design, revision }),
  archive: (id: string, restore = false) => api.post<DraftSummary>(`/composer/drafts/${id}/archive`, { restore }),
  publish: (id: string, note: string) =>
    api.post<{ case_type: string; version: string; changes: string[]; draft: DraftSummary }>(`/composer/drafts/${id}/publish`, { note }),
  validate: (design: Design) => api.post<Check>('/composer/validate', { design }),
  trial: (design: Design, sample: Sample, steps: string[]) => api.post<Trial>('/composer/trial', { design, sample, steps }),
  import: (content: string, filename = '', format?: string) =>
    api.post<{ design: Design; notes: string[]; format: string; errors: number }>('/composer/import', { content, filename, format }),
  export: (design: Design, format: string) => api.post<{ filename: string; media_type: string; content: string }>('/composer/export', { design, format }),
  openCase: (case_type: string, party_id: string, priority = 'medium') =>
    api.post<{ case_id: string }>('/composer/cases', { case_type, party_id, priority }),
}

/** Save text as a file in the browser. */
export function saveFile(filename: string, content: string, type: string) {
  const url = URL.createObjectURL(new Blob([content], { type }))
  const a = Object.assign(document.createElement('a'), { href: url, download: filename })
  a.click()
  URL.revokeObjectURL(url)
}

export const OPS: { id: string; label: string; kinds: string[] }[] = [
  { id: '==', label: 'is', kinds: ['number', 'bool', 'choice', 'text'] },
  { id: '!=', label: 'is not', kinds: ['number', 'bool', 'choice', 'text'] },
  { id: '>=', label: 'is at least', kinds: ['number'] },
  { id: '>', label: 'is more than', kinds: ['number'] },
  { id: '<=', label: 'is at most', kinds: ['number'] },
  { id: '<', label: 'is less than', kinds: ['number'] },
]
