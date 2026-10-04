import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ArrowLeft,
  Briefcase,
  Download,
  Github,
  Globe,
  Linkedin,
  Mail,
  Phone,
  RefreshCw,
  Star,
} from 'lucide-react'

import { ApiError, api } from '@/api/client'
import { candidatesApi } from '@/api/candidates'
import type { Candidate } from '@/api/types'
import { Button } from '@/components/Form'
import { EmptyState, ErrorState, LoadingBlock, PageHeader } from '@/components/Feedback'
import { StatusBadge, Tag } from '@/components/Score'
import { formatDate, formatPercent } from '@/lib/format'
import { useToast } from '@/context/ToastContext'
import { useAsync } from '@/hooks/useAsync'

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-5">
      <h2 className="card-title mb-3">{title}</h2>
      {children}
    </section>
  )
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex flex-wrap justify-between gap-2 border-b border-slate-100 py-2 last:border-0">
      <dt className="text-sm text-slate-500">{label}</dt>
      <dd className="text-sm font-medium text-slate-800">{value || '—'}</dd>
    </div>
  )
}

export default function CandidateDetailPage() {
  const { candidateId } = useParams<{ candidateId: string }>()
  const toast = useToast()
  const id = Number(candidateId)
  const [isReparsing, setIsReparsing] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)

  const { data, error, isLoading, execute } = useAsync(() =>
    Number.isFinite(id) ? candidatesApi.get(id) : Promise.reject(new Error('Invalid candidate id')),
  )

  const handleReparse = async () => {
    setIsReparsing(true)
    try {
      const result = await candidatesApi.reparse(id)
      toast.success(
        `Reparsed ${result.candidate.full_name}.` +
          (result.warnings.length > 0 ? ` ${result.warnings.length} warning(s).` : ''),
      )
      void execute()
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Reparse failed.')
    } finally {
      setIsReparsing(false)
    }
  }

  const handleDownload = async () => {
    setIsDownloading(true)
    try {
      const response = await api.get<Blob>(`/candidates/${id}/resume`, { responseType: 'blob' })
      const url = URL.createObjectURL(response)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = data?.original_filename ?? `resume-${id}`
      document.body.appendChild(anchor)
      anchor.click()
      anchor.remove()
      URL.revokeObjectURL(url)
    } catch {
      toast.error('No original resume file is stored for this candidate.')
    } finally {
      setIsDownloading(false)
    }
  }

  const toggleShortlist = async () => {
    if (!data) return
    try {
      await candidatesApi.setShortlist(id, !data.shortlisted)
      toast.success(data.shortlisted ? 'Removed from shortlist.' : 'Added to shortlist.')
      void execute()
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Could not update the shortlist.')
    }
  }

  if (isLoading) return <LoadingBlock label="Loading candidate" />
  if (error) return <ErrorState message={error.message} onRetry={() => void execute()} />
  if (!data) return null

  const candidate: Candidate = data
  const employment = candidate.employment_history ?? []

  return (
    <div>
      <Link
        to="/candidates"
        className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-900"
      >
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        All candidates
      </Link>

      <PageHeader
        title={candidate.full_name}
        description={[candidate.current_title, candidate.location].filter(Boolean).join(' · ')}
        actions={
          <>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void toggleShortlist()}
              icon={
                <Star className={`h-4 w-4 ${candidate.shortlisted ? 'fill-amber-400 text-amber-400' : ''}`} />
              }
            >
              {candidate.shortlisted ? 'Shortlisted' : 'Shortlist'}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={isReparsing}
              onClick={() => void handleReparse()}
              icon={<RefreshCw className="h-4 w-4" aria-hidden="true" />}
            >
              Reparse
            </Button>
            <Button
              variant="secondary"
              size="sm"
              loading={isDownloading}
              onClick={() => void handleDownload()}
              icon={<Download className="h-4 w-4" aria-hidden="true" />}
            >
              Original file
            </Button>
          </>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <StatusBadge status={candidate.status} />
        {candidate.original_filename ? (
          <span className="text-xs text-slate-500">Uploaded from {candidate.original_filename}</span>
        ) : (
          <span className="text-xs text-slate-500">Manually created profile</span>
        )}
        <span className="text-xs text-slate-500">Added {formatDate(candidate.created_at)}</span>
      </div>

      {candidate.status === 'failed' && candidate.processing_error ? (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-800" role="alert">
          <p className="font-semibold">Parsing failed</p>
          <p className="mt-1">{candidate.processing_error}</p>
          <p className="mt-2 text-xs">
            You can reparse after fixing the source file, or edit the fields manually.
          </p>
        </div>
      ) : null}

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Section title="Summary">
            <dl>
              <InfoRow label="Total experience" value={`${candidate.total_experience_years} years`} />
              <InfoRow
                label="Relevant experience"
                value={`${candidate.relevant_experience_years} years`}
              />
              <InfoRow label="Highest degree" value={candidate.highest_degree} />
              <InfoRow label="Field of study" value={candidate.degree_field} />
              <InfoRow label="Institution" value={candidate.institution} />
              <InfoRow
                label="Graduation year"
                value={candidate.graduation_year ? String(candidate.graduation_year) : null}
              />
            </dl>
          </Section>

          <Section title={`Skills (${candidate.skills.length})`}>
            {candidate.skills.length === 0 ? (
              <EmptyState
                title="No skills detected"
                description="The parser did not find recognisable skills. Reparse or add them manually."
              />
            ) : (
              <ul className="flex flex-wrap gap-2">
                {candidate.skills.map((entry) => (
                  <li
                    key={entry.skill.id}
                    title={
                      entry.years_experience
                        ? `${entry.years_experience} years · confidence ${formatPercent(entry.confidence)}`
                        : `Confidence ${formatPercent(entry.confidence)}`
                    }
                  >
                    <Tag tone={entry.is_certified ? 'emerald' : 'brand'}>
                      {entry.skill.name}
                      {entry.years_experience ? ` · ${entry.years_experience}y` : ''}
                    </Tag>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title={`Employment history (${employment.length})`}>
            {employment.length === 0 ? (
              <EmptyState title="No employment history parsed" />
            ) : (
              <ol className="space-y-4">
                {employment.map((entry, index) => (
                  <li key={`${entry.company}-${index}`} className="border-l-2 border-brand-200 pl-4">
                    <p className="text-sm font-semibold text-slate-900">{entry.title ?? 'Unknown role'}</p>
                    <p className="text-sm text-slate-600">
                      {entry.company ?? 'Unknown company'}
                      {entry.start || entry.end
                        ? ` · ${entry.start ?? '?'} – ${entry.end ?? 'present'}`
                        : ''}
                    </p>
                    {entry.raw ? <p className="mt-1 text-xs text-slate-500">{entry.raw}</p> : null}
                  </li>
                ))}
              </ol>
            )}
          </Section>

          {candidate.previous_positions.length > 0 ? (
            <Section title="Previous positions">
              <div className="flex flex-wrap gap-2">
                {candidate.previous_positions.map((position) => (
                  <Tag key={position}>{position}</Tag>
                ))}
              </div>
            </Section>
          ) : null}

          {candidate.certifications.length > 0 ? (
            <Section title="Certifications">
              <div className="flex flex-wrap gap-2">
                {candidate.certifications.map((certification) => (
                  <Tag key={certification} tone="emerald">
                    {certification}
                  </Tag>
                ))}
              </div>
            </Section>
          ) : null}
        </div>

        <aside className="space-y-6">
          <Section title="Contact">
            <ul className="space-y-3 text-sm">
              {candidate.email ? (
                <li className="flex items-center gap-2 text-slate-700">
                  <Mail className="h-4 w-4 text-slate-400" aria-hidden="true" />
                  <a href={`mailto:${candidate.email}`} className="truncate hover:text-brand-600">
                    {candidate.email}
                  </a>
                </li>
              ) : null}
              {candidate.phone ? (
                <li className="flex items-center gap-2 text-slate-700">
                  <Phone className="h-4 w-4 text-slate-400" aria-hidden="true" />
                  <a href={`tel:${candidate.phone}`} className="hover:text-brand-600">
                    {candidate.phone}
                  </a>
                </li>
              ) : null}
              {candidate.linkedin_url ? (
                <li className="flex items-center gap-2 text-slate-700">
                  <Linkedin className="h-4 w-4 text-slate-400" aria-hidden="true" />
                  <a
                    href={candidate.linkedin_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="truncate hover:text-brand-600"
                  >
                    LinkedIn
                  </a>
                </li>
              ) : null}
              {candidate.github_url ? (
                <li className="flex items-center gap-2 text-slate-700">
                  <Github className="h-4 w-4 text-slate-400" aria-hidden="true" />
                  <a
                    href={candidate.github_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="truncate hover:text-brand-600"
                  >
                    GitHub
                  </a>
                </li>
              ) : null}
              {candidate.portfolio_url ? (
                <li className="flex items-center gap-2 text-slate-700">
                  <Globe className="h-4 w-4 text-slate-400" aria-hidden="true" />
                  <a
                    href={candidate.portfolio_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="truncate hover:text-brand-600"
                  >
                    Portfolio
                  </a>
                </li>
              ) : null}
              {candidate.email || candidate.phone || candidate.linkedin_url || candidate.github_url ? null : (
                <li className="text-slate-400">No contact details parsed.</li>
              )}
            </ul>
          </Section>

          <Section title="Match this candidate">
            <p className="text-sm text-slate-600">
              Scores appear on a job's ranking after an analysis runs. Compare several candidates side by
              side before deciding.
            </p>
            <div className="mt-4 space-y-2">
              <Link to="/jobs" className="btn-secondary btn-sm w-full justify-center">
                <Briefcase className="h-4 w-4" aria-hidden="true" />
                View job rankings
              </Link>
              <Link to="/compare" className="btn-secondary btn-sm w-full justify-center">
                Compare candidates
              </Link>
            </div>
          </Section>

          <Section title="Handling notes">
            <p className="text-xs leading-relaxed text-slate-500">
              Extracted data is derived from the candidate's own document and is only as accurate as that
              document. Confirm anything important with the candidate before acting on it. This profile
              must not be used to make automated hiring decisions.
            </p>
          </Section>
        </aside>
      </div>
    </div>
  )
}