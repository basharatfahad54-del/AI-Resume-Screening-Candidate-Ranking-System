import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Briefcase, Plus, Search, Trash2 } from 'lucide-react'

import { jobsApi } from '@/api/jobs'
import { ApiError } from '@/api/client'
import { Button } from '@/components/Form'
import { EmptyState, ErrorState, PageHeader, SkeletonTable } from '@/components/Feedback'
import { Tag } from '@/components/Score'
import { formatDate } from '@/lib/format'
import { useToast } from '@/context/ToastContext'
import { useAsync } from '@/hooks/useAsync'
import { useDebounced } from '@/hooks/useDebounced'

const PAGE_SIZE = 12

export default function JobsPage() {
  const toast = useToast()
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pendingDelete, setPendingDelete] = useState<number | null>(null)
  const debouncedSearch = useDebounced(search)

  const { data, error, isLoading, execute } = useAsync(() =>
    jobsApi.list({
      search: debouncedSearch || undefined,
      page,
      page_size: PAGE_SIZE,
    }),
  )

  const handleDelete = async (id: number) => {
    setPendingDelete(id)
    try {
      await jobsApi.remove(id)
      toast.success('Job deleted.')
      void execute()
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Delete failed.')
    } finally {
      setPendingDelete(null)
    }
  }

  const totalPages = data?.pages ?? 1

  return (
    <div>
      <PageHeader
        title="Jobs"
        description="Every requisition in the workspace, with the skills it screens for."
        actions={
          <Link to="/jobs/new" className="btn-primary btn-sm">
            <Plus className="h-4 w-4" aria-hidden="true" />
            New job
          </Link>
        }
      />

      <div className="card">
        <div className="card-header">
          <div className="relative w-full max-w-sm">
            <label className="sr-only" htmlFor="job-search">
              Search jobs
            </label>
            <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-3 text-slate-400">
              <Search className="h-4 w-4" aria-hidden="true" />
            </span>
            <input
              id="job-search"
              className="input pl-9"
              placeholder="Search by title, department, or skill"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value)
                setPage(1)
              }}
            />
          </div>
          <p className="text-sm text-slate-500">
            {data ? `${data.total} job${data.total === 1 ? '' : 's'}` : ''}
          </p>
        </div>

        {isLoading ? (
          <SkeletonTable rows={6} columns={4} />
        ) : error ? (
          <div className="p-5">
            <ErrorState message={error.message} onRetry={() => void execute()} />
          </div>
        ) : !data || data.results.length === 0 ? (
          <EmptyState
            title={debouncedSearch ? 'No jobs match that search' : 'No jobs yet'}
            description={
              debouncedSearch
                ? 'Try a different title, department, or skill.'
                : 'Create a requisition to start screening candidates.'
            }
            icon={<Briefcase className="h-6 w-6" />}
            action={
              debouncedSearch ? (
                <Button variant="secondary" size="sm" onClick={() => setSearch('')}>
                  Clear search
                </Button>
              ) : (
                <Link to="/jobs/new" className="btn-primary btn-sm">
                  Create a job
                </Link>
              )
            }
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-slate-200">
              <thead className="bg-slate-50">
                <tr>
                  <th scope="col" className="table-head">
                    Job
                  </th>
                  <th scope="col" className="table-head">
                    Requirements
                  </th>
                  <th scope="col" className="table-head">
                    Status
                  </th>
                  <th scope="col" className="table-head">
                    Created
                  </th>
                  <th scope="col" className="table-head text-right">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.results.map((job) => (
                  <tr key={job.id} className="transition hover:bg-slate-50">
                    <td className="table-cell">
                      <Link to={`/jobs/${job.id}`} className="font-semibold text-slate-900 hover:text-brand-600">
                        {job.title}
                      </Link>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {[job.department, job.location, job.employment_type].filter(Boolean).join(' · ') ||
                          'No location or type set'}
                      </p>
                    </td>
                    <td className="table-cell">
                      <div className="flex max-w-sm flex-wrap gap-1.5">
                        {job.required_skills.slice(0, 4).map((skill) => (
                          <Tag key={skill} tone="brand">
                            {skill}
                          </Tag>
                        ))}
                        {job.required_skills.length > 4 ? (
                          <Tag>+{job.required_skills.length - 4} more</Tag>
                        ) : job.required_skills.length === 0 ? (
                          <span className="text-xs text-slate-400">No required skills listed</span>
                        ) : null}
                      </div>
                    </td>
                    <td className="table-cell">
                      {job.is_active ? <Tag tone="green">Active</Tag> : <Tag tone="slate">Archived</Tag>}
                    </td>
                    <td className="table-cell text-slate-500">{formatDate(job.created_at)}</td>
                    <td className="table-cell text-right">
                      <Button
                        variant="danger"
                        size="sm"
                        loading={pendingDelete === job.id}
                        onClick={() => void handleDelete(job.id)}
                        icon={<Trash2 className="h-3.5 w-3.5" aria-hidden="true" />}
                      >
                        <span className="sr-only">Delete {job.title}</span>
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && data.pages > 1 ? (
          <nav
            className="flex items-center justify-between border-t border-slate-100 px-5 py-3 text-sm"
            aria-label="Jobs pagination"
          >
            <span className="text-slate-500">
              Page {data.page} of {data.pages}
            </span>
            <div className="flex gap-2">
              <Button
                variant="secondary"
                size="sm"
                disabled={!data.page || data.page <= 1}
                onClick={() => setPage((current) => Math.max(1, current - 1))}
              >
                Previous
              </Button>
              <Button
                variant="secondary"
                size="sm"
                disabled={page >= totalPages}
                onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              >
                Next
              </Button>
            </div>
          </nav>
        ) : null}
      </div>
    </div>
  )
}