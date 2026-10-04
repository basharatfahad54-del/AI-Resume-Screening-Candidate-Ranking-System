import { Fragment, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Download, Play, Star, Users } from 'lucide-react'

import { ApiError } from '@/api/client'
import { candidatesApi } from '@/api/candidates'
import { jobsApi } from '@/api/jobs'
import { matchingApi } from '@/api/matching'
import type { Ranking } from '@/api/types'
import { Button } from '@/components/Form'
import { EmptyState, ErrorState, LoadingBlock, PageHeader } from '@/components/Feedback'
import { ScoreBar, Tag } from '@/components/Score'
import { formatDate, formatPercent } from '@/lib/format'
import { useToast } from '@/context/ToastContext'
import { useAsync } from '@/hooks/useAsync'
import MatchDetailPanel from '@/components/MatchDetailPanel'

const COMPONENTS = [
  ['required_skills', 'Required skills'],
  ['experience', 'Experience'],
  ['education', 'Education'],
  ['preferred_skills', 'Preferred skills'],
  ['semantic', 'Semantic'],
  ['certifications', 'Certifications'],
] as const

export default function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const toast = useToast()
  const id = Number(jobId)

  const [sortBy, setSortBy] = useState<'overall' | 'experience' | 'education' | 'skills'>('overall')
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [exportFormat, setExportFormat] = useState<'csv' | 'json'>('csv')
  const [openMatch, setOpenMatch] = useState<number | null>(null)

  const job = useAsync(() => (Number.isFinite(id) ? jobsApi.get(id) : Promise.reject(new Error('Invalid job id'))))
  const ranking = useAsync<Ranking | null>(() =>
    Number.isFinite(id) ? matchingApi.ranking(id, { sort_by: sortBy }) : Promise.reject(new Error('Invalid job id')),
  )

  const runAnalysis = async () => {
    setIsAnalyzing(true)
    try {
      const result = await matchingApi.analyze({ job_id: id, force: false })
      toast.success(
        `Analysed ${result.analyzed} candidate${result.analyzed === 1 ? '' : 's'} in ${(
          result.duration_ms / 1000
        ).toFixed(1)}s. ${result.failed} failed.`,
      )
      void ranking.execute()
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Analysis failed.')
    } finally {
      setIsAnalyzing(false)
    }
  }

  const handleExport = async () => {
    try {
      await matchingApi.exportResults({ job_id: id, format: exportFormat, include_explanation: true })
      toast.success('Export downloaded.')
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Export failed.')
    }
  }

  const toggleShortlist = async (candidateId: number, next: boolean) => {
    try {
      await candidatesApi.setShortlist(candidateId, next)
      toast.success(next ? 'Added to shortlist.' : 'Removed from shortlist.')
      void ranking.execute()
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Could not update the shortlist.')
    }
  }

  if (job.isLoading) return <LoadingBlock label="Loading job" />
  if (job.error) return <ErrorState message={job.error.message} onRetry={() => void job.execute()} />
  if (!job.data) return null

  const data = ranking.data
  const hasResults = Boolean(data && data.results.length > 0)

  return (
    <div>
      <Link to="/jobs" className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-900">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        All jobs
      </Link>

      <PageHeader
        title={job.data.title}
        description={[job.data.department, job.data.location, job.data.employment_type]
          .filter(Boolean)
          .join(' · ')}
        actions={
          <>
            <Link to="/candidates" className="btn-secondary btn-sm">
              <Users className="h-4 w-4" aria-hidden="true" />
              Candidates
            </Link>
            <Button size="sm" loading={isAnalyzing} onClick={() => void runAnalysis()}>
              <Play className="h-4 w-4" aria-hidden="true" />
              Run analysis
            </Button>
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <section className="card p-5 lg:col-span-2">
          <h2 className="card-title">Requirements</h2>
          <dl className="mt-4 grid gap-4 sm:grid-cols-2">
            <div>
              <dt className="label">Experience required</dt>
              <dd className="text-sm text-slate-800">{job.data.experience_required_years} years</dd>
            </div>
            <div>
              <dt className="label">Education required</dt>
              <dd className="text-sm text-slate-800">{job.data.education_required || 'Not specified'}</dd>
            </div>
          </dl>
          <div className="mt-4 space-y-4">
            {(
              [
                ['Required skills', job.data.required_skills, 'brand'],
                ['Preferred skills', job.data.preferred_skills, 'slate'],
                ['Certifications', job.data.certifications, 'emerald'],
                ['Soft skills', job.data.soft_skills, 'slate'],
              ] as const
            ).map(([label, items, tone]) => (
              <div key={label}>
                <p className="label">{label}</p>
                <div className="flex flex-wrap gap-1.5">
                  {items.length === 0 ? (
                    <span className="text-sm text-slate-400">None</span>
                  ) : (
                    items.map((item) => (
                      <Tag key={item} tone={tone}>
                        {item}
                      </Tag>
                    ))
                  )}
                </div>
              </div>
            ))}
          </div>
          {job.data.description ? (
            <div className="mt-5 border-t border-slate-100 pt-4">
              <p className="label">Description</p>
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-600">
                {job.data.description}
              </p>
            </div>
          ) : null}
        </section>

        <aside className="card h-fit p-5">
          <h2 className="card-title">Export</h2>
          <p className="mt-2 text-sm text-slate-600">
            Downloads the ranking with per-component scores and explanations for offline review.
          </p>
          <div className="mt-4 space-y-3">
            <label className="block text-sm font-medium text-slate-700">
              Format
              <select
                className="input mt-1"
                value={exportFormat}
                onChange={(event) => setExportFormat(event.target.value as 'csv' | 'json')}
              >
                <option value="csv">CSV</option>
                <option value="json">JSON</option>
              </select>
            </label>
            <Button
              variant="secondary"
              className="w-full"
              disabled={!hasResults}
              onClick={() => void handleExport()}
              icon={<Download className="h-4 w-4" aria-hidden="true" />}
            >
              Download export
            </Button>
          </div>
          <p className="mt-4 border-t border-slate-100 pt-4 text-xs leading-relaxed text-slate-500">
            Created {formatDate(job.data.created_at)}. Scores are decision support, not an automated
            rejection. A recruiter reviews every result before any decision is communicated.
          </p>
        </aside>
      </div>

      <section className="card mt-6">
        <div className="card-header">
          <h2 className="card-title">Ranking</h2>
          <div className="flex items-center gap-2">
            <label className="sr-only" htmlFor="sort-by">
              Sort by
            </label>
            <select
              id="sort-by"
              className="input py-1.5 text-sm"
              value={sortBy}
              onChange={(event) => setSortBy(event.target.value as typeof sortBy)}
            >
              {COMPONENTS.map(([value, label]) => (
                <option key={value} value={value === 'required_skills' ? 'skills' : value}>
                  Sort by {label.toLowerCase()}
                </option>
              ))}
            </select>
          </div>
        </div>

        {ranking.isLoading ? (
          <LoadingBlock label="Loading ranking" />
        ) : ranking.error ? (
          <div className="p-5">
            <ErrorState message={ranking.error.message} onRetry={() => void ranking.execute()} />
          </div>
        ) : !hasResults ? (
          <EmptyState
            title="No ranking yet"
            description="Run the analysis to score every parsed candidate against this job."
            icon={<Play className="h-6 w-6" />}
            action={
              <Button loading={isAnalyzing} onClick={() => void runAnalysis()}>
                Run analysis
              </Button>
            }
          />
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200">
                <thead className="bg-slate-50">
                  <tr>
                    <th scope="col" className="table-head">
                      #
                    </th>
                    <th scope="col" className="table-head">
                      Candidate
                    </th>
                    <th scope="col" className="table-head w-56">
                      Overall
                    </th>
                    {COMPONENTS.map(([key, label]) => (
                      <th key={key} scope="col" className="table-head text-right">
                        {label}
                      </th>
                    ))}
                    <th scope="col" className="table-head text-right">
                      Shortlist
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data!.results.map((entry) => (
                    <Fragment key={entry.match.id}>
                      <tr className="transition hover:bg-slate-50">
                        <td className="table-cell font-semibold tabular-nums text-slate-400">
                          {entry.rank}
                        </td>
                        <td className="table-cell">
                          <Link
                            to={`/candidates/${entry.candidate.id}`}
                            className="font-semibold text-slate-900 hover:text-brand-600"
                          >
                            {entry.candidate.full_name}
                          </Link>
                          <p className="mt-0.5 text-xs text-slate-500">
                            {entry.candidate.current_title ?? 'Title not parsed'} ·{' '}
                            {entry.candidate.total_experience_years}y ·{' '}
                            {entry.candidate.highest_degree ?? 'Degree unknown'}
                          </p>
                        </td>
                        <td className="table-cell">
                          <ScoreBar value={entry.match.overall_score} />
                          <button
                            type="button"
                            className="mt-1 text-xs font-medium text-brand-600 hover:text-brand-700"
                            onClick={() =>
                              setOpenMatch(openMatch === entry.match.id ? null : entry.match.id)
                            }
                          >
                            {openMatch === entry.match.id ? 'Hide explanation' : 'Why this score?'}
                          </button>
                        </td>
                        {COMPONENTS.map(([key]) => (
                          <td
                            key={key}
                            className="table-cell text-right tabular-nums text-slate-600"
                          >
                            {formatPercent(
                              entry.match[`${key}_score` as keyof typeof entry.match] as number,
                            )}
                          </td>
                        ))}
                        <td className="table-cell text-right">
                          <button
                            type="button"
                            aria-pressed={entry.candidate.shortlisted}
                            aria-label={
                              entry.candidate.shortlisted
                                ? `Remove ${entry.candidate.full_name} from shortlist`
                                : `Add ${entry.candidate.full_name} to shortlist`
                            }
                            onClick={() =>
                              void toggleShortlist(entry.candidate.id, !entry.candidate.shortlisted)
                            }
                            className={`rounded-lg p-2 transition ${
                              entry.candidate.shortlisted
                                ? 'bg-amber-100 text-amber-600'
                                : 'text-slate-300 hover:bg-slate-100 hover:text-amber-500'
                            }`}
                          >
                            <Star
                              className={`h-4 w-4 ${entry.candidate.shortlisted ? 'fill-current' : ''}`}
                              aria-hidden="true"
                            />
                          </button>
                        </td>
                      </tr>
                      {openMatch === entry.match.id ? (
                        <tr className="bg-slate-50">
                          <td colSpan={COMPONENTS.length + 3} className="px-4 py-4">
                            <MatchDetailPanel
                              explanation={entry.match.explanation}
                              evidence={entry.match.evidence}
                              disclaimer={data!.disclaimer}
                            />
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
              {data!.total_candidates} candidate{data!.total_candidates === 1 ? '' : 's'} scored.{' '}
              {data!.disclaimer}
            </p>
          </>
        )}
      </section>
    </div>
  )
}