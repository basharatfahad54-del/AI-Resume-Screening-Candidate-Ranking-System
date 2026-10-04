import { useState } from 'react'
import { Database, HardDrive, ScrollText, ShieldCheck, UserPlus } from 'lucide-react'

import { adminApi } from '@/api/admin'
import { ApiError } from '@/api/client'
import type { UserCreate } from '@/api/types'
import { Button, Select, TextInput } from '@/components/Form'
import { ErrorState, LoadingBlock, PageHeader } from '@/components/Feedback'
import { Tag } from '@/components/Score'
import { formatDateTime } from '@/lib/format'
import { useAuth } from '@/context/AuthContext'
import { useToast } from '@/context/ToastContext'
import { useAsync } from '@/hooks/useAsync'

function CreateUserForm({ onCreated }: { onCreated: () => void }) {
  const toast = useToast()
  const [form, setForm] = useState<UserCreate>({
    name: '',
    email: '',
    password: '',
    role: 'recruiter',
  })
  const [error, setError] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError(null)
    setIsSaving(true)
    try {
      await adminApi.createUser(form)
      toast.success(`Created ${form.email}.`)
      setForm({ name: '', email: '', password: '', role: 'recruiter' })
      onCreated()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Could not create the user.')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3" noValidate>
      {error ? (
        <p className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800" role="alert">
          {error}
        </p>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <TextInput
          label="Name"
          name="name"
          required
          value={form.name}
          onChange={(event) => setForm((c) => ({ ...c, name: event.target.value }))}
        />
        <TextInput
          label="Email"
          name="email"
          type="email"
          required
          value={form.email}
          onChange={(event) => setForm((c) => ({ ...c, email: event.target.value }))}
        />
        <TextInput
          label="Temporary password"
          name="password"
          type="password"
          required
          value={form.password}
          onChange={(event) => setForm((c) => ({ ...c, password: event.target.value }))}
          hint="Share it over a secure channel; ask them to change it after signing in."
        />
        <Select
          label="Role"
          name="role"
          value={form.role}
          onChange={(event) => setForm((c) => ({ ...c, role: event.target.value as UserCreate['role'] }))}
          options={[
            { value: 'recruiter', label: 'Recruiter' },
            { value: 'hiring_manager', label: 'Hiring manager' },
            { value: 'admin', label: 'Administrator' },
          ]}
        />
      </div>
      <Button type="submit" loading={isSaving} icon={<UserPlus className="h-4 w-4" aria-hidden="true" />}>
        Create user
      </Button>
    </form>
  )
}

export default function AdminPage() {
  const { user: currentUser } = useAuth()
  const toast = useToast()
  const [creating, setCreating] = useState(false)
  const [purging, setPurging] = useState(false)

  const users = useAsync(() => adminApi.listUsers())
  const audit = useAsync(() => adminApi.auditLogs({ limit: 25 }))
  const storage = useAsync(() => adminApi.storage())

  const deactivate = async (id: number, name: string) => {
    try {
      await adminApi.deactivateUser(id)
      toast.success(`Deactivated ${name}.`)
      void users.execute()
      void audit.execute()
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Deactivation failed.')
    }
  }

  const runPurge = async () => {
    setPurging(true)
    try {
      const result = await adminApi.runRetentionPurge({ dry_run: true })
      toast.info(`Dry run complete: ${JSON.stringify(result)}`)
    } catch (caught) {
      toast.error(caught instanceof ApiError ? caught.message : 'Retention purge failed.')
    } finally {
      setPurging(false)
    }
  }

  return (
    <div>
      <PageHeader
        title="Administration"
        description="Users, audit trail, and storage. Only administrators see this page."
      />

      <div className="space-y-6">
        <section className="card p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="card-title flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-brand-600" aria-hidden="true" />
              Users
            </h2>
            <Button variant="secondary" size="sm" onClick={() => setCreating((current) => !current)}>
              {creating ? 'Cancel' : 'Add user'}
            </Button>
          </div>

          {creating ? (
            <div className="mb-5 rounded-xl border border-slate-200 bg-slate-50 p-4">
              <CreateUserForm onCreated={() => void users.execute()} />
            </div>
          ) : null}

          {users.isLoading ? (
            <LoadingBlock label="Loading users" />
          ) : users.error ? (
            <ErrorState message={users.error.message} onRetry={() => void users.execute()} />
          ) : !users.data || users.data.length === 0 ? (
            <p className="text-sm text-slate-500">No users found.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-slate-200">
                <thead className="bg-slate-50">
                  <tr>
                    <th scope="col" className="table-head">
                      User
                    </th>
                    <th scope="col" className="table-head">
                      Role
                    </th>
                    <th scope="col" className="table-head">
                      Status
                    </th>
                    <th scope="col" className="table-head text-right">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {users.data.map((account) => (
                    <tr key={account.id}>
                      <td className="table-cell">
                        <p className="font-medium text-slate-900">{account.name}</p>
                        <p className="text-xs text-slate-500">{account.email}</p>
                      </td>
                      <td className="table-cell">
                        <Tag tone={account.role === 'admin' ? 'violet' : 'slate'}>{account.role}</Tag>
                      </td>
                      <td className="table-cell">
                        {account.is_active ? <Tag tone="green">Active</Tag> : <Tag tone="red">Disabled</Tag>}
                      </td>
                      <td className="table-cell text-right">
                        {account.is_active && account.id !== currentUser?.id ? (
                          <Button
                            variant="danger"
                            size="sm"
                            onClick={() => void deactivate(account.id, account.name)}
                          >
                            Deactivate
                          </Button>
                        ) : (
                          <span className="text-xs text-slate-400">
                            {account.id === currentUser?.id ? 'That is you' : '—'}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <div className="grid gap-6 lg:grid-cols-2">
          <section className="card p-5">
            <h2 className="card-title mb-4 flex items-center gap-2">
              <ScrollText className="h-4 w-4 text-brand-600" aria-hidden="true" />
              Recent audit trail
            </h2>
            {audit.isLoading ? (
              <LoadingBlock label="Loading audit trail" />
            ) : audit.error ? (
              <ErrorState message={audit.error.message} onRetry={() => void audit.execute()} />
            ) : !audit.data || audit.data.length === 0 ? (
              <p className="text-sm text-slate-500">No audit entries yet.</p>
            ) : (
              <ul className="max-h-80 space-y-2 overflow-y-auto text-sm">
                {audit.data.map((entry) => (
                  <li key={entry.id} className="border-b border-slate-100 pb-2 last:border-0">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="font-medium text-slate-800">{entry.action}</span>
                      <span className="text-xs text-slate-400">{formatDateTime(entry.created_at)}</span>
                    </div>
                    <p className="text-xs text-slate-500">
                      {entry.entity_type ? `${entry.entity_type} #${entry.entity_id}` : 'system event'}
                      {entry.user_id ? ` · user #${entry.user_id}` : ''}
                      {entry.ip_address ? ` · ${entry.ip_address}` : ''}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="card p-5">
            <h2 className="card-title mb-4 flex items-center gap-2">
              <HardDrive className="h-4 w-4 text-brand-600" aria-hidden="true" />
              Storage and retention
            </h2>
            {storage.isLoading ? (
              <LoadingBlock label="Loading storage info" />
            ) : storage.error ? (
              <ErrorState message={storage.error.message} onRetry={() => void storage.execute()} />
            ) : storage.data ? (
              <dl className="space-y-2 text-sm">
                <div className="flex justify-between border-b border-slate-100 py-2">
                  <dt className="text-slate-500">Files stored</dt>
                  <dd className="font-medium tabular-nums text-slate-800">
                    {storage.data.total_files} ({storage.data.total_size_mb} MB)
                  </dd>
                </div>
                <div className="flex justify-between border-b border-slate-100 py-2">
                  <dt className="text-slate-500">Max upload size</dt>
                  <dd className="font-medium text-slate-800">{storage.data.max_file_size_mb} MB</dd>
                </div>
                <div className="flex justify-between border-b border-slate-100 py-2">
                  <dt className="text-slate-500">Allowed types</dt>
                  <dd className="font-medium text-slate-800">
                    {storage.data.allowed_extensions.join(', ')}
                  </dd>
                </div>
                <div className="flex justify-between py-2">
                  <dt className="text-slate-500">Retention</dt>
                  <dd className="font-medium text-slate-800">{storage.data.retention_days} days</dd>
                </div>
              </dl>
            ) : null}

            <div className="mt-4 space-y-2 border-t border-slate-100 pt-4">
              <Button
                variant="secondary"
                size="sm"
                loading={purging}
                onClick={() => void runPurge()}
                icon={<Database className="h-4 w-4" aria-hidden="true" />}
              >
                Preview retention purge
              </Button>
              <p className="text-xs text-slate-500">
                Runs a dry run only. Nothing is deleted until a real purge is executed server-side.
              </p>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}