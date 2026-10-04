import { api } from './client'
import type { AssistantResponse, Dashboard, Health, Skill } from './types'

export const assistantApi = {
  status: () => api.get<Record<string, unknown>>('/assistant/status'),

  query: (payload: {
    message: string
    job_id?: number | null
    conversation_id?: number | null
    top_k?: number
  }) => api.post<AssistantResponse>('/assistant/query', payload),
}

export interface DashboardParams {
  job_id?: number | null
  recent_limit?: number
}

export const dashboardApi = {
  overview: (params: DashboardParams = {}) => api.get<Dashboard>('/dashboard', { params }),
}

export const skillsApi = {
  search: (params: { search?: string; category?: string; limit?: number } = {}) =>
    api.get<Skill[]>('/skills', { params }),

  count: () => api.get<{ count: number }>('/skills/count'),
}

export const systemApi = {
  health: () => api.get<Health>('/health'),
}