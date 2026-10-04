/**
 * Presentation helpers with no React dependency, so they can be used from
 * components, tests, and plain utilities alike.
 */

export type ScoreTone = 'excellent' | 'good' | 'fair' | 'poor'

export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${(value * 100).toFixed(digits)}%`
}

export function scoreTone(value: number | null | undefined): ScoreTone {
  if (value === null || value === undefined) return 'poor'
  if (value >= 0.85) return 'excellent'
  if (value >= 0.7) return 'good'
  if (value >= 0.5) return 'fair'
  return 'poor'
}

export function scoreColour(tone: ScoreTone): string {
  return {
    excellent: 'bg-emerald-500',
    good: 'bg-brand-500',
    fair: 'bg-amber-500',
    poor: 'bg-slate-400',
  }[tone]
}

export function initials(name: string): string {
  return name
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
}

export function formatDate(value: string | null | undefined): string {
  const date = value ? new Date(value) : null
  if (!date || Number.isNaN(date.getTime())) return '—'
  return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function formatDateTime(value: string | null | undefined): string {
  const date = value ? new Date(value) : null
  if (!date || Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}