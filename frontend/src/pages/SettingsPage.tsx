import { useState } from 'react'
import { KeyRound, Sliders, Trash2 } from 'lucide-react'

import { authApi } from '@/api/auth'
import { ApiError } from '@/api/client'
import { matchingApi, WEIGHT_COMPONENTS } from '@/api/matching'
import type { ScoringWeights } from '@/api/types'
import { Button, TextInput } from '@/components/Form'
import { ErrorState, LoadingBlock, PageHeader } from '@/components/Feedback'
import { Tag } from '@/components/Score'
import { formatPercent } from '@/lib/format'
import { useAuth } from '@/context/AuthContext'
import { useToast } from '@/context/ToastContext'
import { useAsync } from '@/hooks/useAsync'

function PasswordSection() {
  const toast = useToast()
  const [form, setForm] = useState({ current_password: '', new_password: '', confirm: '' })
  const [error, setError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    if (form.new_password !== form.confirm) {
      setError('New passwords do not match.')
      return
    }
    setIsSaving(true)
    try {
      await authApi.changePassword({
        current_password: form.current_password,
        new_password: form.new_password,
      })
      toast.success('Password updated.')
      setForm({ current_password: '', new_password: '', confirm: '' })
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not change the password.')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" noValidate>
      {error ? (
        <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">
          {error}
        </p>
      ) : null}
      <TextInput
        label="Current password"
        name="current_password"
        type="password"
        autoComplete="current-password"
        required
        value={form.current_password}
        onChange={(event) => setForm((c) => ({ ...c, current_password: event.target.value }))}
      />
      <TextInput
        label="New password"
        name="new_password"
        type="password"
        autoComplete="new-password"
        required
        value={form.new_password}
        onChange={(event) => setForm((c) => ({ ...c, new_password: event.target.value }))}
        hint="Minimum 8 characters, including a letter and a number."
      />
      <TextInput
        label="Confirm new password"
        name="confirm"
        type="password"
        autoComplete="new-password"
        required
        value={form.confirm}
        onChange={(event) => setForm((c) => ({ ...c, confirm: event.target.value }))}
      />
      <Button type="submit" loading={isSaving}>
        Update password
      </Button>
    </form>
  )
}

function WeightsSection() {
  const toast = useToast()
  const [draft, setDraft] = useState<ScoringWeights | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [pendingDelete, setPendingDelete] = useState<number | null>(null)

  const profiles = useAsync(() => matchingApi.listWeightProfiles())
  const defaults = useAsync(() => matchingApi.defaultWeights())

  const total = draft
    ? Object.values(draft).reduce((sum, value) => sum + value, 0)
    : 0

  const saveDefault = async () => {
    if (!draft) return
    setIsSaving(true)
    try {
      // Editing the default profile is expressed as "create, then make default",
      // which keeps the existing audit trail intact.
      await matchingApi.createWeightProfile({
        name: `Custom ${new Date().toLocaleDateString()}`,
        description: 'Edited from the settings screen',
        weights: draft,
        is_default: true,
      })
      toast.success('Default weights updated.')
      setDraft(null)
      void profiles.execute()
      void defaults.execute()
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Could not save the weights.')
    } finally {
      setIsSaving(false)
    }
  }

  const remove = async (id: number) => {
    setPendingDelete(id)
    try {
      await matchingApi.deleteWeightProfile(id)
      toast.success('Weight profile deleted.')
      void profiles.execute()
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Delete failed.')
    } finally {
      setPendingDelete(null)
    }
  }

  const active = draft ?? defaults.data ?? null

  return (
    <div>
      {defaults.isLoading ? (
        <LoadingBlock label="Loading weights" />
      ) : defaults.error ? (
        <ErrorState message={defaults.error.message} onRetry={() => void defaults.execute()} />
      ) : active ? (
        <div className="space-y-4">
          <p className="text-sm text-slate-600">
            Weights control how much each component contributes to the overall score. They are stored as
            profiles so any past analysis stays reproducible.
          </p>

          <div className="space-y-4">
            {WEIGHT_COMPONENTS.map((component) => {
              const value = active[component.key]
              const percent = total > 0 ? (value / total) * 100 : 0
              return (
                <div key={component.key}>
                  <div className="flex items-baseline justify-between">
                    <label
                      className="text-sm font-medium text-slate-700"
                      htmlFor={`weight-${component.key}`}
                    >
                      {component.label}
                    </label>
                    <span className="text-sm tabular-nums text-slate-500">
                      {value.toFixed(2)} ({percent.toFixed(0)}%)
                    </span>
                  </div>
                  <input
                    id={`weight-${component.key}`}
                    type="range"
                    min={0}
                    max={1}
                    step={0.05}
                    className="mt-1 w-full accent-brand-600"
                    value={value}
                    onChange={(event) => {
                      const next = { ...active, [component.key]: Number(event.target.value) }
                      setDraft(next)
                    }}
                  />
                  <p className="text-xs text-slate-500">{component.hint}</p>
                </div>
              )
            })}
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-slate-100 pt-4">
            <Button loading={isSaving} disabled={!draft} onClick={() => void saveDefault()}>
              Save as new default
            </Button>
            {draft ? (
              <Button variant="ghost" onClick={() => setDraft(null)}>
                Discard changes
              </Button>
            ) : null}
            <span className="text-xs text-slate-500">Total weight: {total.toFixed(2)}</span>
          </div>
        </div>
      ) : null}

      {profiles.data && profiles.data.length > 0 ? (
        <div className="mt-6 border-t border-slate-100 pt-4">
          <h3 className="text-sm font-semibold text-slate-800">Saved profiles</h3>
          <ul className="mt-3 space-y-2">
            {profiles.data.map((profile) => (
              <li
                key={profile.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 px-3 py-2"
              >
                <div className="min-w-0">
                  <p className="text-sm font-medium text-slate-800">
                    {profile.name}
                    {profile.is_default ? (
                      <Tag tone="brand">default</Tag>
                    ) : null}
                  </p>
                  <p className="text-xs text-slate-500">
                    Required {formatPercent(profile.weights.required_skills)} · Experience{' '}
                    {formatPercent(profile.weights.experience)} · Semantic{' '}
                    {formatPercent(profile.weights.semantic)}
                  </p>
                </div>
                {profile.is_default ? null : (
                  <Button
                    variant="danger"
                    size="sm"
                    loading={pendingDelete === profile.id}
                    onClick={() => void remove(profile.id)}
                    icon={<Trash2 className="h-3.5 w-3.5" aria-hidden="true" />}
                  >
                    <span className="sr-only">Delete {profile.name}</span>
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}

export default function SettingsPage() {
  const { user } = useAuth()

  return (
    <div>
      <PageHeader title="Settings" description="Account security and scoring configuration." />

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="card p-5">
          <h2 className="card-title mb-4 flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-brand-600" aria-hidden="true" />
            Password
          </h2>
          <p className="mb-4 text-sm text-slate-600">
            Signed in as {user?.email} ({user?.role}).
          </p>
          <PasswordSection />
        </section>

        <section className="card p-5">
          <h2 className="card-title mb-4 flex items-center gap-2">
            <Sliders className="h-4 w-4 text-brand-600" aria-hidden="true" />
            Scoring weights
          </h2>
          <WeightsSection />
        </section>
      </div>
    </div>
  )
}