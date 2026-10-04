import { useState } from 'react'
import { Scale, Search } from 'lucide-react'

import { ApiError } from '@/api/client'
import { candidatesApi } from '@/api/candidates'
import { matchingApi } from '@/api/matching'
import type { Comparison } from '@/api/types'
import { Button } from '@/components/Form'
import { EmptyState, ErrorState, LoadingBlock, PageHeader } from '@/components/Feedback'
import { useToast } from '@/context/ToastContext'
import { useAsync } from '@/hooks/useAsync'
import { useDebounced } from '@/hooks/useDebounced'

const MAX_CANDIDATES = 5

export default function ComparePage() {
  const toast = useToast()
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<number[]>([])
  const [comparison, setComparison] = useState<Comparison | null>(null)
  const [isComparing, setIsComparing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const debouncedSearch = useDebounced(search)
  const results = useAsync(() =>
    candidatesApi.list({ search: debouncedSearch || undefined, page_size: 20 }),
  )

  const toggle = (id: number) => {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((value) => value !== id)
      if (current.length >= MAX_CANDIDATES) {
        toast.error(`Compare at most ${MAX_CANDIDATES} candidates at once.`)
        return current
      }
      return [...current, id]
    })
  }

  const runComparison = async () => {
    if (selected.length < 2) {
      setError('Select at least two candidates.')
      return
    }
    setError(null)
    setIsComparing(true)
    try {
      const result = await matchingApi.compare({ candidate_ids: selected })
      setComparison(result)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Comparison failed.')
    } finally {
      setIsComparing(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="Compare candidates"
        description="A factual side-by-side of extracted resume data. No overall recommendation is produced."
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="card lg:col-span-1">
          <div className="card-header">
            <h2 className="card-title">Select candidates</h2>
            <span className="text-sm text-slate-500">{selected.length} selected</span>
          </div>

          <div className="border-b border-slate-100 p-4">
            <div className="relative">
              <label className="sr-only" htmlFor="compare-search">
                Search candidates
              </label>
              <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-slate-400">
                <Search className="h-4 w-4" aria-hidden="true" />
              </span>
              <input
                id="compare-search"
                className="input pl-9"
                placeholder="Search candidates"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
          </div>

          {results.isLoading ? (
            <LoadingBlock label="Loading candidates" />
          ) : results.error ? (
            <div className="p-4">
              <ErrorState message={results.error.message} onRetry={() => void results.execute()} />
            </div>
          ) : !results.data || results.data.results.length === 0 ? (
            <EmptyState title="No candidates found" description="Upload resumes first." />
          ) : (
            <ul className="max-h-96 divide-y divide-slate-100 overflow-y-auto">
              {results.data.results.map((candidate) => (
                <li key={candidate.id}>
                  <label className="flex cursor-pointer items-center gap-3 px-4 py-3 text-sm hover:bg-slate-50">
                    <input
                      type="checkbox"
                      checked={selected.includes(candidate.id)}
                      onChange={() => toggle(candidate.id)}
                      className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
                    />
                    <span className="min-w-0">
                      <span className="block truncate font-medium text-slate-800">
                        {candidate.full_name}
                      </span>
                      <span className="block truncate text-xs text-slate-500">
                        {candidate.current_title ?? 'No title parsed'}
                      </span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          )}

          <div className="border-t border-slate-100 p-4">
            {error ? (
              <p className="mb-2 text-xs font-medium text-red-600" role="alert">
                {error}
              </p>
            ) : null}
            <Button
              className="w-full"
              loading={isComparing}
              disabled={selected.length < 2}
              onClick={() => void runComparison()}
              icon={<Scale className="h-4 w-4" aria-hidden="true" />}
            >
              Compare {selected.length || ''}
            </Button>
          </div>
        </section>

        <section className="lg:col-span-2">
          {!comparison ? (
            <div className="card">
              <EmptyState
                title="No comparison yet"
                description="Select two or more candidates and compare their extracted experience, education, and skills."
                icon={<Scale className="h-6 w-6" />}
              />
            </div>
          ) : (
            <div className="space-y-6">
              <div className="card overflow-x-auto">
                <table className="min-w-full divide-y divide-slate-200">
                  <thead className="bg-slate-50">
                    <tr>
                      <th scope="col" className="table-head">
                        Criterion
                      </th>
                      {comparison.columns.map((column) => (
                        <th key={column} scope="col" className="table-head text-right">
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {comparison.rows.map((row) => (
                      <tr key={row.criterion} className="align-top">
                        <th scope="row" className="px-4 py-3 text-left text-sm font-medium text-slate-700">
                          {row.label}
                          {row.note ? <span className="mt-0.5 block text-xs font-normal text-slate-500">{row.note}</span> : null}
                        </th>
                        {comparison.columns.map((column) => {
                          const isBest = row.best.includes(column)
                          return (
                            <td
                              key={column}
                              className={`px-4 py-3 text-right text-sm tabular-nums ${
                                isBest ? 'font-semibold text-emerald-700' : 'text-slate-600'
                              }`}
                            >
                              {String(row.values[column] ?? '—')}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="card overflow-x-auto">
                <div className="card-header">
                  <h2 className="card-title">Skills matrix</h2>
                </div>
                <table className="min-w-full divide-y divide-slate-200">
                  <thead className="bg-slate-50">
                    <tr>
                      <th scope="col" className="table-head">
                        Skill
                      </th>
                      {comparison.columns.map((column) => (
                        <th key={column} scope="col" className="table-head text-center">
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {Object.entries(comparison.skills_matrix).map(([skill, matrix]) => (
                      <tr key={skill}>
                        <th scope="row" className="px-4 py-2 text-left text-sm font-medium text-slate-700">
                          {skill}
                        </th>
                        {comparison.columns.map((column) => (
                          <td key={column} className="px-4 py-2 text-center">
                            {matrix[column] ? (
                              <span className="font-bold text-emerald-600" aria-label="Has skill">
                                ✓
                              </span>
                            ) : (
                              <span className="text-slate-300" aria-label="Missing skill">
                                —
                              </span>
                            )}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <p className="text-xs leading-relaxed text-slate-500">{comparison.disclaimer}</p>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}