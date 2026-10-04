import { api } from './client'
import type { AuditLog, StorageInfo, User, UserCreate, UserRole } from './types'

export interface AuditLogParams {
  user_id?: number
  action?: string
  limit?: number
}

export const adminApi = {
  listUsers: () => api.get<User[]>('/admin/users'),

  createUser: (payload: UserCreate) => api.post<User>('/admin/users', payload),

  deactivateUser: (id: number) => api.delete<void>(`/admin/users/${id}`),

  auditLogs: (params: AuditLogParams = {}) => api.get<AuditLog[]>('/admin/audit-logs', { params }),

  storage: () => api.get<StorageInfo>('/admin/storage'),

  roles: () => api.get<{ roles: UserRole[] }>('/admin/roles'),

  runRetentionPurge: (payload: { delete_resumes?: boolean; dry_run?: boolean } = {}) =>
    api.post<Record<string, unknown>>('/admin/retention/purge', payload),

  purgeCandidate: (id: number) =>
    api.post<Record<string, unknown>>(`/admin/candidates/${id}/purge`),
}