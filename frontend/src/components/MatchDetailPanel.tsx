import { useState } from 'react'
import { ChevronDown, Info } from 'lucide-react'

import type { MatchEvidence, MatchExplanation } from '@/api/types'

const STATUS_STYLES: Record<string, string> = {
  matched: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  partial: 'bg-amber-50 text-amber-700 border-amber-200',
  missing: 'bg-red-50 text-red-700 border-red-200',
  met: 'bg-emerald-50 text-emerald-700 border-emerald-200',
  unknown: 'bg-slate-50 text-slate-600 border-slate-200',
}

function statusClass(status: string): string {
  return STATUS_STYLES[status.toLowerCase()] ?? STATUS_STYLES.unknown
}

function EvidenceGroup({ title, items, tone }: { title: string; items: string[]; tone: string }) {
  if (items.length === 0) return null
  return (
    <div>
      <p className={`text-xs font-semibold uppercase tracking-wide ${tone}`}>{title}</p>
      <ul className="mt-1 space-y-1">
        {items.map((item) => (
          <li key={item} className="text-sm text-slate-700">
            {item}
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * The "why" behind a score. This is the most important panel in the product:
 * a recruiter should never see a number without the evidence that produced it.
 */
export default function MatchDetailPanel({
  explanation,
  evidence,
  disclaimer,
}: {
  explanation: MatchExplanation | null
  evidence: MatchEvidence[]
  disclaimer?: string
}) {
  const [showEvidence, setShowEvidence] = useState(false)

  return (
    <div className="space-y-4">
      {explanation ? (
        <>
          <p className="text-sm font-medium text-slate-800">{explanation.summary}</p>
          <div className="grid gap-4 sm:grid-cols-2">
            <EvidenceGroup title="Strong matches" items={explanation.strong_matches} tone="text-emerald-700" />
            <EvidenceGroup title="Missing required" items={explanation.missing_required} tone="text-red-700" />
            <EvidenceGroup title="Preferred matches" items={explanation.preferred_matches} tone="text-brand-700" />
            <EvidenceGroup title="Weak signals" items={explanation.weak_signals} tone="text-amber-700" />
          </div>
          {explanation.recommendation ? (
            <p className="rounded-lg bg-slate-100 p-3 text-sm text-slate-700">
              <span className="font-semibold">Suggested next step: </span>
              {explanation.recommendation}
            </p>
          ) : null}
        </>
      ) : (
        <p className="text-sm text-slate-500">
          No structured explanation was stored for this score.
        </p>
      )}

      {evidence.length > 0 ? (
        <div>
          <button
            type="button"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-brand-600 hover:text-brand-700"
            onClick={() => setShowEvidence((current) => !current)}
            aria-expanded={showEvidence}
          >
            {showEvidence ? 'Hide' : 'Show'} {evidence.length} evidence item
            {evidence.length === 1 ? '' : 's'}
            <ChevronDown className={`h-4 w-4 transition ${showEvidence ? 'rotate-180' : ''}`} />
          </button>
          {showEvidence ? (
            <ul className="mt-3 space-y-2">
              {evidence.map((item, index) => (
                <li
                  key={`${item.component}-${index}`}
                  className="rounded-lg border border-slate-200 bg-white p-3"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-medium text-slate-800">{item.label}</span>
                    <span className={`badge border ${statusClass(item.status)}`}>{item.status}</span>
                  </div>
                  <p className="mt-1 text-sm text-slate-600">{item.detail}</p>
                  {item.snippet ? (
                    <p className="mt-2 border-l-2 border-slate-200 pl-3 font-mono text-xs text-slate-500">
                      {item.snippet}
                    </p>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {disclaimer ? (
        <p className="flex items-start gap-2 text-xs text-slate-500">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {disclaimer}
        </p>
      ) : null}
    </div>
  )
}