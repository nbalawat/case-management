import { describe, expect, it } from 'vitest'
import { fromSortParam, toSortParam } from './useListParams'

describe('sort in the address', () => {
  it('round-trips a multi-column sort', () => {
    const s = [{ id: 'priority', desc: false }, { id: 'created', desc: true }]
    expect(toSortParam(s)).toBe('priority,-created')
    expect(fromSortParam('priority,-created')).toEqual(s)
  })
  it('treats an empty value as the default order', () => {
    expect(fromSortParam('')).toEqual([])
    expect(toSortParam([])).toBe('')
  })
})
