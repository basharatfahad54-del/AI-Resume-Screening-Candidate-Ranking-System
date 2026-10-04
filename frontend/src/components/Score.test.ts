import { describe, expect, it } from 'vitest'

import { formatPercent, initials, scoreTone } from '@/lib/format'

describe('formatPercent', () => {
  it('renders an API score as a percentage', () => {
    expect(formatPercent(0.8254)).toBe('83%')
    expect(formatPercent(0.8254, 1)).toBe('82.5%')
  })

  it('renders a dash instead of NaN or 0% for missing scores', () => {
    expect(formatPercent(null)).toBe('—')
    expect(formatPercent(undefined)).toBe('—')
    expect(formatPercent(Number.NaN)).toBe('—')
  })

  it('keeps a genuine zero distinct from a missing score', () => {
    expect(formatPercent(0)).toBe('0%')
  })
})

describe('scoreTone', () => {
  it('bands scores consistently', () => {
    expect(scoreTone(0.9)).toBe('excellent')
    expect(scoreTone(0.75)).toBe('good')
    expect(scoreTone(0.55)).toBe('fair')
    expect(scoreTone(0.2)).toBe('poor')
    expect(scoreTone(null)).toBe('poor')
  })
})

describe('initials', () => {
  it('uses at most two characters', () => {
    expect(initials('Ada Lovelace')).toBe('AL')
    expect(initials('Prince')).toBe('P')
    expect(initials('  Grace  Brewster  Hopper ')).toBe('GB')
  })
})