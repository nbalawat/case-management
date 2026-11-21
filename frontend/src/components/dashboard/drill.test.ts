import { describe, expect, it } from 'vitest'
import { casesHref, drillChips } from './drill'

describe('casesHref', () => {
  it('leaves out the default open status and empty values', () => {
    expect(casesHref({})).toBe('/cases')
    expect(casesHref({ status: 'open', due: 'week' })).toBe('/cases?due=week')
    expect(casesHref({ status: 'all', case_type: 'kyc', opened: '2026-11' })).toBe('/cases?status=all&case_type=kyc&opened=2026-11')
  })
})

describe('drillChips', () => {
  it('describes each drill filter in words', () => {
    const chips = drillChips(new URLSearchParams('due=overdue&scenario=structuring&opened=2026-11&closed=2026-11-03'), s => s.toUpperCase())
    expect(chips.map(c => c.label)).toEqual(['Past the deadline', 'Alert scenario: STRUCTURING', 'Opened in November 2026', 'Closed on Nov 3, 2026'])
  })
  it('ignores ordinary filters', () => expect(drillChips(new URLSearchParams('priority=high&status=all'))).toEqual([]))
})
