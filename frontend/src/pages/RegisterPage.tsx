import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { AlertCircle, Mail, User } from 'lucide-react'

import AuthLayout from './AuthLayout'
import { Button, Field, TextInput } from '@/components/Form'
import { ApiError } from '@/api/client'
import { useAuth } from '@/context/AuthContext'
import { useToast } from '@/context/ToastContext'

/** Mirror of the backend policy so the user gets feedback before a round-trip. */
function passwordIssues(password: string): string[] {
  const issues: string[] = []
  if (password.length < 8) issues.push('at least 8 characters')
  if (!/[A-Za-z]/.test(password)) issues.push('a letter')
  if (!/\d/.test(password)) issues.push('a number')
  return issues
}

export default function RegisterPage() {
  const { register } = useAuth()
  const toast = useToast()
  const navigate = useNavigate()

  const [form, setForm] = useState({ name: '', email: '', password: '', confirm: '' })
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)

  const issues = useMemo(() => passwordIssues(form.password), [form.password])

  const update = (key: keyof typeof form) => (event: React.ChangeEvent<HTMLInputElement>) =>
    setForm((current) => ({ ...current, [key]: event.target.value }))

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setFieldErrors({})

    if (form.password !== form.confirm) {
      setFieldErrors({ confirm: 'Passwords do not match.' })
      return
    }
    if (issues.length > 0) {
      setFieldErrors({ password: `Password needs ${issues.join(', ')}.` })
      return
    }

    setIsSubmitting(true)
    try {
      const user = await register({
        name: form.name.trim(),
        email: form.email.trim(),
        password: form.password,
      })
      toast.success(`Account created. Welcome, ${user.name.split(' ')[0]}.`)
      navigate('/', { replace: true })
    } catch (caught) {
      if (caught instanceof ApiError) {
        setFieldErrors(caught.fieldErrors())
        setError(caught.message)
      } else {
        setError(caught instanceof Error ? caught.message : 'Unable to create the account.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="New accounts are recruiter accounts. An administrator can change roles later."
      footer={
        <>
          Already registered?{' '}
          <Link to="/login" className="font-semibold text-brand-600 hover:text-brand-700">
            Sign in
          </Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        {error ? (
          <div
            className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800"
            role="alert"
          >
            <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span>{error}</span>
          </div>
        ) : null}

        <TextInput
          label="Full name"
          name="name"
          autoComplete="name"
          required
          icon={<User className="h-4 w-4" />}
          value={form.name}
          onChange={update('name')}
          error={fieldErrors.name}
        />

        <TextInput
          label="Work email"
          name="email"
          type="email"
          autoComplete="email"
          required
          icon={<Mail className="h-4 w-4" />}
          value={form.email}
          onChange={update('email')}
          error={fieldErrors.email}
        />

        <TextInput
          label="Password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          value={form.password}
          onChange={update('password')}
          error={fieldErrors.password}
          hint="Minimum 8 characters, including a letter and a number."
        />

        <TextInput
          label="Confirm password"
          name="confirm"
          type="password"
          autoComplete="new-password"
          required
          value={form.confirm}
          onChange={update('confirm')}
          error={fieldErrors.confirm}
        />

        <Field label="Role">
          <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-600">
            Recruiter
          </div>
        </Field>

        <Button type="submit" loading={isSubmitting} className="w-full">
          Create account
        </Button>
      </form>
    </AuthLayout>
  )
}