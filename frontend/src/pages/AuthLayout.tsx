import type { ReactNode } from 'react'

/** Shared chrome for the signed-out screens so login and register stay identical. */
export default function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string
  subtitle: string
  children: ReactNode
  footer?: ReactNode
}) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col justify-center px-6 py-12 sm:px-12">
        <div className="mx-auto w-full max-w-md">
          <div className="mb-8 flex items-center gap-3">
            <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand-600 text-lg font-bold text-white">
              T
            </span>
            <div>
              <p className="text-lg font-bold leading-tight text-slate-900">TalentMatch AI</p>
              <p className="text-xs text-slate-500">Resume screening &amp; ranking</p>
            </div>
          </div>

          <h1 className="text-2xl font-bold tracking-tight text-slate-900">{title}</h1>
          <p className="mt-1 text-sm text-slate-500">{subtitle}</p>

          <div className="mt-8">{children}</div>

          {footer ? <div className="mt-6 text-sm text-slate-500">{footer}</div> : null}
        </div>
      </div>

      <div className="relative hidden overflow-hidden bg-navy-900 lg:block">
        <div
          className="absolute inset-0 opacity-25"
          style={{
            backgroundImage:
              'radial-gradient(circle at 20% 20%, #6366f1 0, transparent 45%), radial-gradient(circle at 80% 60%, #0ea5e9 0, transparent 45%)',
          }}
          aria-hidden="true"
        />
        <div className="relative flex h-full flex-col justify-center px-14 text-white">
          <blockquote className="max-w-md text-2xl font-semibold leading-snug">
            Screening that shows its reasoning, so a human stays in the loop.
          </blockquote>
          <p className="mt-6 max-w-md text-sm leading-relaxed text-white/70">
            TalentMatch parses resumes, extracts structured requirements, and ranks candidates with a
            transparent weighted score. Every score is decision support for a recruiter — the system
            never rejects anyone on its own, and it never uses protected characteristics.
          </p>
          <ul className="mt-8 space-y-3 text-sm text-white/80">
            <li className="flex items-center gap-3">
              <span className="h-1.5 w-1.5 rounded-full bg-brand-400" />
              Evidence-backed scoring with per-skill matches and gaps
            </li>
            <li className="flex items-center gap-3">
              <span className="h-1.5 w-1.5 rounded-full bg-brand-400" />
              CSV and JSON export for offline review
            </li>
            <li className="flex items-center gap-3">
              <span className="h-1.5 w-1.5 rounded-full bg-brand-400" />
              Structured, rule-based assistant queries over your own data
            </li>
          </ul>
        </div>
      </div>
    </div>
  )
}