import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  Briefcase,
  FileWarning,
  FileText,
  Search,
  Star,
  TrendingUp,
  Users,
} from 'lucide-react'

import { dashboardApi } from '@/api'
import { EmptyState, ErrorState, PageHeader, SkeletonTable } from '@/components/Feedback'
import { StatCard, StatusBadge, Tag } from '@/components/Score'
import { formatDate, formatPercent } from '@/lib/format'
import { useAsync } from '@/hooks/useAsync'

const PIE_COLOURS = ['#0f766e', '#4f46e5', '#0891b2', '#7c3aed', '#ea580c', '#16a34a']

export default function DashboardPage() {
  // "30d" is sent as the recent-candidate window; the endpoint takes an optional job filter too.
  const { data, error, isLoading, execute } = useAsync(() => dashboardApi.overview({ recent_limit: 8 }))

  const [pieIndex, setPieIndex] = useState(0)

  const pieData = useMemo(() => {
    const buckets = data?.score_distribution ?? []
    return buckets
      .filter((bucket) => bucket.count > 0)
      .map((bucket) => ({ name: bucket.bucket, value: bucket.count }))
  }, [data])

  if (isLoading) {
    return (
      <div>
        <PageHeader title="Dashboard" description="Portfolio-wide screening health at a glance." />
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="card h-28 animate-pulse bg-slate-100" />
          ))}
        </div>
        <div className="card mt-6">
          <SkeletonTable />
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div>
        <PageHeader title="Dashboard" />
        <ErrorState message={error.message} onRetry={() => void execute()} />
      </div>
    )
  }

  if (!data) return null

  const { stats } = data
  const isEmpty = stats.total_candidates === 0 && stats.active_jobs === 0

  if (isEmpty) {
    return (
      <div>
        <PageHeader title="Dashboard" description="Portfolio-wide screening health at a glance." />
        <div className="card">
          <EmptyState
            title="No data yet"
            description="Create a job, upload resumes, then run the match analysis to populate this dashboard."
            icon={<Search className="h-6 w-6" />}
            action={
              <Link to="/jobs/new" className="btn-primary btn-sm">
                Create your first job
              </Link>
            }
          />
        </div>
      </div>
    )
  }

  return (
    <div>
      <PageHeader
        title="Dashboard"
        description="Portfolio-wide screening health at a glance."
        actions={
          <Link to="/assistant" className="btn-secondary btn-sm">
            Ask the assistant
          </Link>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Total candidates"
          value={stats.total_candidates}
          icon={<Users className="h-5 w-5" />}
          tone="brand"
          hint={`${stats.candidates_processed} parsed`}
        />
        <StatCard
          label="Active jobs"
          value={stats.active_jobs}
          icon={<Briefcase className="h-5 w-5" />}
          tone="violet"
        />
        <StatCard
          label="Average match score"
          value={formatPercent(stats.average_match_score, 1)}
          icon={<TrendingUp className="h-5 w-5" />}
          tone="emerald"
        />
        <StatCard
          label="Shortlisted"
          value={stats.shortlisted_candidates}
          icon={<Star className="h-5 w-5" />}
          tone="amber"
          hint={stats.failed_processing > 0 ? `${stats.failed_processing} failed to parse` : undefined}
        />
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <section className="card lg:col-span-2">
          <div className="card-header">
            <h2 className="card-title">Candidates per job</h2>
          </div>
          {data.candidates_per_job.length === 0 ? (
            <EmptyState title="No scored jobs yet" description="Run an analysis from a job page." />
          ) : (
            <div className="h-72 p-4">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data.candidates_per_job} margin={{ top: 8, right: 8, bottom: 8, left: 0 }}>
                  <XAxis dataKey="job_title" tick={{ fontSize: 12 }} interval={0} tickFormatter={(value: string) => (value.length > 18 ? `${value.slice(0, 17)}…` : value)} />
                  <YAxis allowDecimals={false} tick={{ fontSize: 12 }} width={28} />
                  <ChartTooltip
                    cursor={{ fill: 'rgba(15, 23, 42, 0.04)' }}
                    formatter={(value: number, _name, item) => [
                      `${value} candidates`,
                      `${(item.payload as { average_score: number }).average_score.toFixed(2)} avg score`,
                    ]}
                  />
                  <Bar dataKey="candidate_count" fill="#0f766e" radius={[6, 6, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          )}
        </section>

        <section className="card">
          <div className="card-header">
            <h2 className="card-title">Score distribution</h2>
          </div>
          {pieData.length === 0 ? (
            <EmptyState title="No scores yet" />
          ) : (
            <>
              <div className="h-52 p-4">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={pieData}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={45}
                      outerRadius={75}
                      paddingAngle={2}
                      onMouseEnter={(_, index) => setPieIndex(index)}
                    >
                      {pieData.map((entry, index) => (
                        <Cell
                          key={entry.name}
                          fill={PIE_COLOURS[index % PIE_COLOURS.length]}
                          opacity={pieIndex === index ? 1 : 0.75}
                        />
                      ))}
                    </Pie>
                    <ChartTooltip />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <ul className="space-y-1 px-5 pb-4 text-sm text-slate-600">
                {pieData.map((entry, index) => (
                  <li key={entry.name} className="flex items-center justify-between">
                    <span className="flex items-center gap-2">
                      <span
                        className="h-2.5 w-2.5 rounded-full"
                        style={{ backgroundColor: PIE_COLOURS[index % PIE_COLOURS.length] }}
                        aria-hidden="true"
                      />
                      {entry.name}
                    </span>
                    <span className="tabular-nums font-medium text-slate-900">{entry.value}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-3">
        <section className="card lg:col-span-2">
          <div className="card-header">
            <h2 className="card-title">Recent candidates</h2>
            <Link to="/candidates" className="text-sm font-medium text-brand-600 hover:text-brand-700">
              View all
            </Link>
          </div>
          {data.recent_candidates.length === 0 ? (
            <EmptyState
              title="No candidates uploaded"
              description="Upload PDF or DOCX resumes from the Candidates page."
              icon={<FileText className="h-6 w-6" />}
            />
          ) : (
            <ul className="divide-y divide-slate-100">
              {data.recent_candidates.map((candidate) => (
                <li key={candidate.id} className="flex items-center justify-between gap-4 px-5 py-3">
                  <div className="min-w-0">
                    <Link
                      to={`/candidates/${candidate.id}`}
                      className="truncate font-medium text-slate-900 hover:text-brand-600"
                    >
                      {candidate.full_name}
                    </Link>
                    <p className="truncate text-xs text-slate-500">
                      {candidate.current_title ?? 'Title not parsed'}
                      {candidate.original_filename ? ` · ${candidate.original_filename}` : ''}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    {candidate.status === 'failed' ? (
                      <Tag tone="red">
                        <FileWarning className="h-3 w-3" aria-hidden="true" /> Failed
                      </Tag>
                    ) : (
                      <StatusBadge status={candidate.status} />
                    )}
                    <span className="hidden text-xs text-slate-400 sm:inline">
                      {formatDate(candidate.created_at)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <div className="space-y-6">
          <section className="card">
            <div className="card-header">
              <h2 className="card-title">Top skills</h2>
            </div>
            {data.top_skills.length === 0 ? (
              <EmptyState title="No skills extracted yet" />
            ) : (
              <ul className="flex flex-wrap gap-2 p-5">
                {data.top_skills.slice(0, 14).map((skill) => (
                  <li key={skill.normalized_name}>
                    <Tag tone="brand">
                      {skill.skill}
                      <span className="ml-1 text-slate-400">{skill.count}</span>
                    </Tag>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card">
            <div className="card-header">
              <h2 className="card-title">Most missing skills</h2>
            </div>
            {data.most_missing_skills.length === 0 ? (
              <EmptyState title="Nothing missing" description="No candidate gaps detected yet." />
            ) : (
              <ul className="divide-y divide-slate-100">
                {data.most_missing_skills.slice(0, 8).map((item) => (
                  <li key={`${item.job_id}-${item.skill}`} className="flex items-center justify-between px-5 py-2.5">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-slate-800">{item.skill}</p>
                      <Link
                        to={`/jobs/${item.job_id}`}
                        className="truncate text-xs text-slate-500 hover:text-brand-600"
                      >
                        Job #{item.job_id}
                      </Link>
                    </div>
                    <span className="shrink-0 text-sm font-semibold tabular-nums text-red-600">
                      {item.missing_count}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>
    </div>
  )
}