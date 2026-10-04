import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, Check, FileUp, Sparkles, Type } from 'lucide-react'

import { jobsApi } from '@/api/jobs'
import { ApiError } from '@/api/client'
import type { JobCreate, JobExtraction } from '@/api/types'
import { Button, Field, TextArea, TextInput } from '@/components/Form'
import { PageHeader } from '@/components/Feedback'
import { Tag } from '@/components/Score'
import { useToast } from '@/context/ToastContext'

type Source = 'paste' | 'file' | 'manual'

const STEPS = ['Source', 'Requirements', 'Review'] as const

/** Split a comma/newline separated list coming from a text box. */
function parseList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

function listToText(value: string[] | undefined): string {
  return (value ?? []).join(', ')
}

export default function JobCreatePage() {
  const navigate = useNavigate()
  const toast = useToast()
  const fileInput = useRef<HTMLInputElement>(null)

  const [step, setStep] = useState(0)
  const [source, setSource] = useState<Source>('paste')
  const [description, setDescription] = useState('')
  const [isExtracting, setIsExtracting] = useState(false)
  const [isCreating, setIsCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [form, setForm] = useState<JobCreate>({
    title: '',
    description: '',
    department: '',
    location: '',
    employment_type: '',
    required_skills: [],
    preferred_skills: [],
    certifications: [],
    responsibilities: [],
    soft_skills: [],
    experience_required_years: 0,
    education_required: '',
    weight_profile: null,
  })

  const [lists, setLists] = useState({
    required_skills: '',
    preferred_skills: '',
    certifications: '',
    responsibilities: '',
    soft_skills: '',
  })

  const update = <K extends keyof JobCreate>(key: K, value: JobCreate[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  const applyExtraction = (extraction: JobExtraction) => {
    setForm((current) => ({
      ...current,
      title: extraction.title ?? current.title,
      department: extraction.department ?? current.department,
      location: extraction.location ?? current.location,
      employment_type: extraction.employment_type ?? current.employment_type,
      education_required: extraction.education_required ?? current.education_required,
      experience_required_years: extraction.experience_required_years,
      description: extraction.raw_text || current.description,
    }))
    setLists({
      required_skills: listToText(extraction.required_skills),
      preferred_skills: listToText(extraction.preferred_skills),
      certifications: listToText(extraction.certifications),
      responsibilities: listToText(extraction.responsibilities),
      soft_skills: listToText(extraction.soft_skills),
    })
  }

  const handleParse = async () => {
    if (description.trim().length < 40) {
      setError('Paste at least a few sentences so the extractor has something to work with.')
      return
    }
    setError(null)
    setIsExtracting(true)
    try {
      const extraction = await jobsApi.parse({
        description,
        title: form.title || null,
        persist: false,
      })
      applyExtraction(extraction)
      setStep(1)
      toast.success('Requirements extracted. Review them before saving.')
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not parse that description.')
    } finally {
      setIsExtracting(false)
    }
  }

  const handleFile = async (file: File) => {
    setError(null)
    setIsExtracting(true)
    try {
      // The upload endpoint parses and persists in one call, so it is used for
      // "create straight from a file" and the review step becomes the job page.
      const job = await jobsApi.uploadDescription(file)
      toast.success(`Created "${job.title}" from ${file.name}.`)
      navigate(`/jobs/${job.id}`)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Upload failed.')
      setIsExtracting(false)
    }
  }

  const handleCreate = async () => {
    if (!form.title.trim()) {
      setError('A job title is required.')
      return
    }
    setError(null)
    setIsCreating(true)
    try {
      const payload: JobCreate = {
        ...form,
        title: form.title.trim(),
        required_skills: parseList(lists.required_skills),
        preferred_skills: parseList(lists.preferred_skills),
        certifications: parseList(lists.certifications),
        responsibilities: parseList(lists.responsibilities),
        soft_skills: parseList(lists.soft_skills),
      }
      const job = await jobsApi.create(payload)
      toast.success(`Job "${job.title}" created.`)
      navigate(`/jobs/${job.id}`)
    } catch (caught) {
      if (caught instanceof ApiError) {
        const fieldErrors = caught.fieldErrors()
        setError(
          Object.keys(fieldErrors).length > 0
            ? Object.entries(fieldErrors)
                .map(([field, message]) => `${field}: ${message}`)
                .join(' ')
            : caught.message,
        )
      } else {
        setError(caught instanceof Error ? caught.message : 'Could not create the job.')
      }
    } finally {
      setIsCreating(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="New job"
        description="Paste a description, upload a file, or fill the form in. Extracted values are always editable."
      />

      <ol className="mb-6 flex flex-wrap items-center gap-2 text-sm" aria-label="Progress">
        {STEPS.map((label, index) => (
          <li key={label} className="flex items-center gap-2">
            <span
              className={`grid h-7 w-7 place-items-center rounded-full text-xs font-semibold ${
                index < step
                  ? 'bg-brand-600 text-white'
                  : index === step
                    ? 'bg-brand-100 text-brand-700 ring-2 ring-brand-500'
                    : 'bg-slate-200 text-slate-500'
              }`}
              aria-current={index === step ? 'step' : undefined}
            >
              {index < step ? <Check className="h-4 w-4" aria-hidden="true" /> : index + 1}
            </span>
            <span className={index === step ? 'font-semibold text-slate-900' : 'text-slate-500'}>
              {label}
            </span>
            {index < STEPS.length - 1 ? <span className="h-px w-6 bg-slate-300" aria-hidden="true" /> : null}
          </li>
        ))}
      </ol>

      {error ? (
        <div
          className="mb-5 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
          role="alert"
        >
          {error}
        </div>
      ) : null}

      <div className="card p-5 sm:p-6">
        {step === 0 ? (
          <div className="space-y-5">
            <fieldset>
              <legend className="label">How would you like to start?</legend>
              <div className="grid gap-3 sm:grid-cols-3">
                {(
                  [
                    { id: 'paste', label: 'Paste a description', icon: <Type className="h-4 w-4" /> },
                    { id: 'file', label: 'Upload a file', icon: <FileUp className="h-4 w-4" /> },
                    { id: 'manual', label: 'Fill in manually', icon: <Check className="h-4 w-4" /> },
                  ] as const
                ).map((option) => (
                  <label
                    key={option.id}
                    className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-sm font-medium transition ${
                      source === option.id
                        ? 'border-brand-500 bg-brand-50 text-brand-700 ring-1 ring-brand-500'
                        : 'border-slate-200 text-slate-700 hover:bg-slate-50'
                    }`}
                  >
                    <input
                      type="radio"
                      name="source"
                      value={option.id}
                      checked={source === option.id}
                      onChange={() => setSource(option.id)}
                      className="sr-only"
                    />
                    <span className="grid h-8 w-8 place-items-center rounded-lg bg-white text-slate-500">
                      {option.icon}
                    </span>
                    {option.label}
                  </label>
                ))}
              </div>
            </fieldset>

            {source === 'paste' ? (
              <>
                <TextInput
                  label="Job title"
                  name="title"
                  value={form.title}
                  onChange={(event) => update('title', event.target.value)}
                  placeholder="Senior Backend Engineer"
                  hint="Optional when parsing — the extractor can infer it."
                />
                <TextArea
                  label="Job description"
                  name="description"
                  required
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="Paste the full posting, including requirements and responsibilities."
                  hint={`${description.trim().length} characters. Around 400+ gives the best extraction.`}
                />
                <Button type="button" loading={isExtracting} onClick={() => void handleParse()}>
                  <Sparkles className="h-4 w-4" aria-hidden="true" />
                  Extract requirements
                </Button>
              </>
            ) : null}

            {source === 'file' ? (
              <div className="rounded-xl border-2 border-dashed border-slate-300 p-8 text-center">
                <FileUp className="mx-auto h-8 w-8 text-slate-400" aria-hidden="true" />
                <p className="mt-3 text-sm font-medium text-slate-700">Upload a job description</p>
                <p className="mt-1 text-xs text-slate-500">
                  PDF or DOCX. The file is parsed and the job is created immediately.
                </p>
                <input
                  ref={fileInput}
                  type="file"
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  className="sr-only"
                  onChange={(event) => {
                    const file = event.target.files?.[0]
                    if (file) void handleFile(file)
                  }}
                />
                <Button
                  type="button"
                  className="mt-4"
                  loading={isExtracting}
                  onClick={() => fileInput.current?.click()}
                >
                  Choose file
                </Button>
              </div>
            ) : null}

            {source === 'manual' ? (
              <>
                <TextInput
                  label="Job title"
                  name="title"
                  required
                  value={form.title}
                  onChange={(event) => update('title', event.target.value)}
                />
                <TextArea
                  label="Description"
                  name="description"
                  value={form.description ?? ''}
                  onChange={(event) => update('description', event.target.value)}
                  hint="Optional for manual entry; used as text for the semantic score."
                />
                <Button type="button" onClick={() => setStep(1)}>
                  Continue
                  <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </Button>
              </>
            ) : null}
          </div>
        ) : null}

        {step === 1 ? (
          <div className="space-y-5">
            <TextInput
              label="Job title"
              name="title"
              required
              value={form.title}
              onChange={(event) => update('title', event.target.value)}
            />
            <div className="grid gap-4 sm:grid-cols-2">
              <TextInput
                label="Department"
                name="department"
                value={form.department ?? ''}
                onChange={(event) => update('department', event.target.value)}
              />
              <TextInput
                label="Location"
                name="location"
                value={form.location ?? ''}
                onChange={(event) => update('location', event.target.value)}
              />
              <TextInput
                label="Employment type"
                name="employment_type"
                value={form.employment_type ?? ''}
                onChange={(event) => update('employment_type', event.target.value)}
                placeholder="Full-time"
              />
              <TextInput
                label="Experience required (years)"
                name="experience_required_years"
                type="number"
                min={0}
                max={40}
                step={0.5}
                value={form.experience_required_years ?? 0}
                onChange={(event) => update('experience_required_years', Number(event.target.value))}
              />
            </div>
            <TextInput
              label="Education required"
              name="education_required"
              value={form.education_required ?? ''}
              onChange={(event) => update('education_required', event.target.value)}
              placeholder="Bachelor's degree in Computer Science"
            />

            {(
              [
                ['required_skills', 'Required skills', 'Comma separated. These drive most of the score.'],
                ['preferred_skills', 'Preferred skills', 'Nice-to-have skills.'],
                ['certifications', 'Certifications', 'e.g. AWS Certified Solutions Architect'],
                ['responsibilities', 'Responsibilities', 'Used as text context, not scored directly.'],
                ['soft_skills', 'Soft skills', 'Communication, leadership, and similar.'],
              ] as const
            ).map(([key, label, hint]) => (
              <TextArea
                key={key}
                label={label}
                name={key}
                value={lists[key]}
                hint={hint}
                onChange={(event) => setLists((current) => ({ ...current, [key]: event.target.value }))}
              />
            ))}
          </div>
        ) : null}

        {step === 2 ? (
          <div className="space-y-5">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">{form.title}</h2>
              <p className="mt-1 text-sm text-slate-500">
                {[form.department, form.location, form.employment_type].filter(Boolean).join(' · ') ||
                  'No location or type set'}
              </p>
            </div>

            <dl className="grid gap-4 sm:grid-cols-2">
              <div>
                <dt className="label">Experience required</dt>
                <dd className="text-sm text-slate-800">{form.experience_required_years ?? 0} years</dd>
              </div>
              <div>
                <dt className="label">Education required</dt>
                <dd className="text-sm text-slate-800">{form.education_required || 'Not specified'}</dd>
              </div>
            </dl>

            <div className="space-y-4">
              {(
                [
                  ['Required skills', parseList(lists.required_skills), 'brand'],
                  ['Preferred skills', parseList(lists.preferred_skills), 'slate'],
                  ['Certifications', parseList(lists.certifications), 'emerald'],
                  ['Responsibilities', parseList(lists.responsibilities), 'slate'],
                  ['Soft skills', parseList(lists.soft_skills), 'slate'],
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

            <Field label="Notes">
              <p className="text-sm text-slate-600">
                Saving creates the job. Screening runs only when you start an analysis, so you can edit
                the requirements first.
              </p>
            </Field>
          </div>
        ) : null}

        <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 pt-5">
          <Button
            variant="secondary"
            onClick={() => (step === 0 ? navigate('/jobs') : setStep((current) => current - 1))}
            icon={<ArrowLeft className="h-4 w-4" aria-hidden="true" />}
          >
            {step === 0 ? 'Cancel' : 'Back'}
          </Button>

          {step === 1 ? (
            <Button onClick={() => setStep(2)}>
              Review
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </Button>
          ) : null}

          {step === 2 ? (
            <Button loading={isCreating} onClick={() => void handleCreate()}>
              Create job
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  )
}