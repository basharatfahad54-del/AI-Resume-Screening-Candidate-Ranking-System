import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { FileUp, Search, Star, UploadCloud } from 'lucide-react'

import { ApiError } from '@/api/client'
import { candidatesApi } from '@/api/candidates'
import type { CandidateListParams } from '@/api/candidates'
import { jobsApi } from '@/api/jobs'
import { Button } from '@/components/Form'
import { EmptyState, ErrorState, PageHeader, SkeletonTable } from '@/components/Feedback'
import { StatusBadge, Tag } from '@/components/Score'
import { formatDate } from '@/lib/format'
import { useToast } from '@/context/ToastContext'
import { useAsync } from '@/hooks/useAsync'
import { useDebounced } from '@/hooks/useDebounced'

const PAGE_SIZE = 12
const ACCEPT = '.pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain'

export default function CandidatesPage() {
  const toast = useToast()
  const fileInput = useRef<HTMLInputElement>(null)

  const [search, setSearch] = useState('')
  const [location, setLocation] = useState('')
  const [minExperience, setMinExperience] = useState('')
  const [education, setEducation] = useState('')
  const [certification, setCertification] = useState('')
  const [shortlistedOnly, setShortlistedOnly] = useState(false)
  const [page, setPage] = useState(1)

  const [uploadPercent, setUploadPercent] = useState<number | null>(null)
  const [isDragging, setIsDragging] = useState(false)

  const debouncedSearch = useDebounced(search)
  const debouncedLocation = useDebounced(location)
  const debouncedEducation = useDebounced(education)
  const debouncedCertification = useDebounced(certification)
  const debouncedMinExperience = useDebounced(minExperience)

  const params: CandidateListParams = {
    search: debouncedSearch || undefined,
    location: debouncedLocation || undefined,
    min_experience: debouncedMinExperience ? Number(debouncedMinExperience) : undefined,
    education: debouncedEducation || undefined,
    certification: debouncedCertification || undefined,
    shortlisted_only: shortlistedOnly || undefined,
    page,
    page_size: PAGE_SIZE,
  }

  const { data, error, isLoading, execute } = useAsync(() => candidatesApi.list(params))
  const jobs = useAsync(() => jobsApi.list({ page_size: 100 }))

  const upload = async (files: File[]) => {
    if (files.length === 0) return
    setUploadPercent(0)
    try {
      const result = await candidatesApi.uploadResumes(files, setUploadPercent)
      const warnings = result.results.flatMap((entry) => entry.warnings)
      toast.success(
        `Uploaded ${result.succeeded} of ${result.total} resumes` +
          (result.failed > 0 ? `, ${result.failed} failed.` : '.') +
          (warnings.length > 0 ? ` ${warnings.length} warning(s).` : ''),
      )
      if (result.failed > 0) {
        result.errors.slice(0, 3).forEach((issue) => toast.error(issue.detail ?? JSON.stringify(issue)))
      }
      void execute()
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Upload failed.')
    } finally {
      setUploadPercent(null)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  const resetFilters = () => {
    setSearch('')
    setLocation('')
    setMinExperience('')
    setEducation('')
    setCertification('')
    setShortlistedOnly(false)
    setPage(1)
  }

  const hasFilters =
    Boolean(search || location || minExperience || education || certification) || shortlistedOnly

  return (
    <div>
      <PageHeader
        title="Candidates"
        description="Parsed candidate profiles. Scores appear once a job analysis has run."
      />

      <div className="card mb-6 p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="card-title">Upload resumes</h2>
            <p className="mt-1 text-sm text-slate-500">
              PDF, DOCX, or TXT. Multiple files at once. Text is extracted and parsed automatically.
            </p>
          </div>
          <input
            ref={fileInput}
            type="file"
            multiple
            accept={ACCEPT}
            className="sr-only"
            onChange={(event) => void upload(Array.from(event.target.files ?? []))}
          />
          <Button
            loading={uploadPercent !== null}
            onClick={() => fileInput.current?.click()}
            icon={<UploadCloud className="h-4 w-4" aria-hidden="true" />}
          >
            Choose files
          </Button>
        </div>

        {uploadPercent !== null ? (
          <div className="mt-4" role="status" aria-live="polite">
            <div className="mb-1 flex justify-between text-xs text-slate-600">
              <span>Uploading…</span>
              <span>{uploadPercent}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-slate-200">
              <div
                className="h-full rounded-full bg-brand-600 transition-all duration-200"
                style={{ width: `${uploadPercent}%` }}
              />
            </div>
          </div>
        ) : null}

        <div
          className={`mt-4 rounded-xl border-2 border-dashed p-4 text-center text-xs transition ${
            isDragging ? 'border-brand-500 bg-brand-50' : 'border-slate-200'
          }`}
          onDragOver={(event) => {
            event.preventDefault()
            setIsDragging(true)
          }}
          onDragLeave={() => setIsDragging(false)}
          onDrop={(event) => {
            event.preventDefault()
            setIsDragging(false)
            void upload(Array.from(event.dataTransfer.files))
          }}
        >
          Or drop files here. Re-uploading a resume for the same email updates that candidate rather
          than creating a duplicate.
        </div>
      </div>

      <div className="card">
        <div className="card-header">
          <div className="relative w-full max-w-sm">
            <label className="sr-only" htmlFor="candidate-search">
              Search candidates
            </label>
            <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-slate-400">
              <Search className="h-4 w-4" aria-hidden="true" />
            </span>
            <input
              id="candidate-search"
              className="input pl-9"
              placeholder="Name, email, skill, company"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                setPage(1)
              }}
            />
          </div>
          {hasFilters ? (
            <Button variant="ghost" size="sm" onClick={resetFilters}>
              Clear filters
            </Button>
          ) : null}
        </div>

        <div className="grid gap-3 border-b border-slate-100 px-5 py-4 sm:grid-cols-2 lg:grid-cols-5">
          <input
            className="input py-1.5 text-sm"
            placeholder="Location"
            aria-label="Filter by location"
            value={location}
            onChange={(event) => {
              setLocation(event.target.value)
              setPage(1)
            }}
          />
          <input
            className="input py-1.5 text-sm"
            placeholder="Min experience (years)"
            aria-label="Minimum experience in years"
            type="number"
            min={0}
            value={minExperience}
            onChange={(event) => {
              setMinExperience(event.target.value)
              setPage(1)
            }}
          />
          <input
            className="input py-1.5 text-sm"
            placeholder="Education (e.g. Bachelor)"
            aria-label="Filter by education"
            value={education}
            onChange={(event) => {
              setEducation(event.target.value)
              setPage(1)
            }}
          />
          <input
            className="input py-1.5 text-sm"
            placeholder="Certification"
            aria-label="Filter by certification"
            value={certification}
            onChange={(event) => {
              setCertification(event.target.value)
              setPage(1)
            }}
          />
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={shortlistedOnly}
              onChange={(event) => {
                setShortlistedOnly(event.target.checked)
                setPage(1)
              }}
              className="h-4 w-4 rounded border-slate-300 text-brand-600 focus:ring-brand-500"
            />
            Shortlisted only
          </label>
        </div>

        {isLoading ? (
          <SkeletonTable rows={6} columns={5} />
        ) : error ? (
          <div className="p-5">
            <ErrorState message={error.message} onRetry={() => void execute()} />
          </div>
        ) : !data || data.results.length === 0 ? (
          <EmptyState
            title={hasFilters ? 'No candidates match those filters' : 'No candidates yet'}
            description={
              hasFilters
                ? 'Loosen a filter to see more results.'
                : 'Upload resumes above, or ask the assistant to find candidates by skill.'
            }
            icon={<FileUp className="h-6 w-6" />}
            action={
              hasFilters ? (
                <Button variant="secondary" size="sm" onClick={resetFilters}>
                  Clear filters
                </Button>
              ) : null
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th scope="col" className="table-head">
                    Candidate
                  </th>
                  <th scope="col" className="table-head">
                    Top skills
                  </th>
                  <th scope="col" className="table-head">
                    Experience
                  </th>
                  <th scope="col" className="table-head">
                    Status
                  </th>
                  <th scope="col" className="table-head">
                    Added
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.results.map((candidate) => (
                  <tr key={candidate.id} className="transition hover:bg-slate-50">
                    <td className="table-cell">
                      <div className="flex items-center gap-2">
                        <Link
                          to={`/candidates/${candidate.id}`}
                          className="font-semibold text-slate-900 hover:text-brand-600"
                        >
                          {candidate.full_name}
                        </Link>
                        {candidate.shortlisted ? (
                          <Star className="h-4 w-4 fill-amber-400 text-amber-400" aria-label="Shortlisted" />
                        ) : null}
                      </div>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {[candidate.current_title, candidate.location].filter(Boolean).join(' · ') ||
                          'No title or location parsed'}
                      </p>
                    </td>
                    <td className="table-cell">
                      <div className="flex max-w-xs flex-wrap gap-1.5">
                        {candidate.top_skills.length === 0 ? (
                          <span className="text-xs text-slate-400">None detected</span>
                        ) : (
                          candidate.top_skills.slice(0, 4).map((skill) => (
                            <Tag key={skill}>{skill}</Tag>
                          ))
                        )}
                      </div>
                    </td>
                    <td className="table-cell tabular-nums">{candidate.total_experience_years}y</td>
                    <td className="table-cell">
                      <StatusBadge status={candidate.status} />
                    </td>
                    <td className="table-cell text-slate-500">{formatDate(candidate.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && data.pages > 1 ? (
          <nav
            className="flex items-center justify-between border-t border-slate-100 px-5 py-3 text-sm"
            aria-label="Candidates pagination"
          >
            <span className="text-slate-500">
              Page {data.page} of {data.pages} · {data.total} candidates
            </span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= data.pages}
                onClick={() => setPage((current) => Math.min(data.pages, current + 1))}
              >
                Next
              </Button>
            </div>
          </nav>
        ) : null}
      </div>

      {jobs.data && jobs.data.results.length > 0 ? (
        <p className="mt-4 text-xs text-slate-500">
          Tip: open a job and run the analysis to see these candidates ranked.{' '}
          <Link to="/jobs" className="font-medium text-brand-600 hover:text-brand-700">
            Go to jobs
          </Link>
        </p>
      ) : null}
    </div>
  )
}