import type { ReactNode } from 'react'

import { formatPercent, scoreColour, scoreTone } from '@/lib/format'

export function ScoreBadge({
  value,
  size = 'md',
  label,
}: {
  value: number | null | undefined
  size?: 'sm' | 'md' | 'lg'
  label?: string
}) {
  const sizing = {
    sm: 'text-xs px-2 py-0.5',
    md: 'text-sm px-2.5 py-1',
    lg: 'text-lg px-3 py-1.5',
  }[size]

  return (
    <span
      className={`badge font-semibold tabular-nums text-white ${scoreColour(scoreTone(value))} ${sizing}`}
      title={label ?? 'Match score'}
    >
      {formatPercent(value)}
    </span>
  )
}

export function ScoreBar({
  value,
  showValue = true,
  className = '',
}: {
  value: number | null | undefined
  showValue?: boolean
  className?: string
}) {
  const percent = value === null || value === undefined ? 0 : Math.round(value * 100)
  return (
    <div className={`flex items-center gap-2 ${className}`}>
      <div
        className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200"
        role="progressbar"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Match score"
      >
        <div
          className={`h-full rounded-full transition-all duration-500 ${scoreColour(scoreTone(value))}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      {showValue ? (
        <span className="w-11 shrink-0 text-right text-xs font-semibold tabular-nums text-slate-700">
          {formatPercent(value)}
        </span>
      ) : null}
    </div>
  )
}

const STATUS_STYLES: Record<string, string> = {
  parsed: 'bg-emerald-100 text-emerald-800',
  parsing: 'bg-amber-100 text-amber-800',
  uploaded: 'bg-slate-100 text-slate-700',
  failed: 'bg-red-100 text-red-800',
}

export function StatusBadge({ status }: { status: string }) {
  return (
    <span className={`badge capitalize ${STATUS_STYLES[status] ?? 'bg-slate-100 text-slate-700'}`}>
      {status}
    </span>
  )
}

export function StatCard({
  label,
  value,
  icon,
  hint,
  tone = 'brand',
}: {
  label: string
  value: string | number
  icon?: ReactNode
  hint?: string
  tone?: 'brand' | 'emerald' | 'amber' | 'violet'
}) {
  const tones = {
    brand: 'bg-brand-50 text-brand-600',
    emerald: 'bg-emerald-50 text-emerald-600',
    amber: 'bg-amber-50 text-amber-600',
    violet: 'bg-violet-50 text-violet-600',
  }
  return (
    <div className="card p-5 transition hover:shadow-card-hover">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-slate-500">{label}</p>
          <p className="mt-2 text-2xl font-bold tabular-nums text-slate-900">{value}</p>
          {hint ? <p className="mt-1 truncate text-xs text-slate-400">{hint}</p> : null}
        </div>
        {icon ? (
          <span className={`shrink-0 rounded-xl p-2.5 ${tones[tone]}`} aria-hidden="true">
            {icon}
          </span>
        ) : null}
      </div>
    </div>
  )
}

export function Tag({ children, tone = 'slate' }: { children: ReactNode; tone?: string }) {
  const tones: Record<string, string> = {
    slate: 'bg-slate-100 text-slate-700',
    brand: 'bg-brand-50 text-brand-700',
    emerald: 'bg-emerald-50 text-emerald-700',
    amber: 'bg-amber-50 text-amber-700',
    red: 'bg-red-50 text-red-700',
    green: 'bg-green-50 text-green-700',
    violet: 'bg-violet-50 text-violet-700',
  }
  return <span className={`badge ${tones[tone] ?? tones.slate}`}>{children}</span>
}