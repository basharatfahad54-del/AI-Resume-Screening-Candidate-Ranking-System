import { api, upload } from './client'
import type {
  Job,
  JobCandidateCount,
  JobCreate,
  JobExtraction,
  JobUpdate,
  Page,
} from './types'

export interface JobListParams {
  search?: string
  is_active?: boolean
  page?: number
  page_size?: number
}

export const jobsApi = {
  list: (params: JobListParams = {}) =>
    api.get<Page<Job>>('/jobs', { params }),

  get: (id: number) => api.get<Job>(`/jobs/${id}`),

  create: (payload: JobCreate) => api.post<Job>('/jobs', payload),

  update: (id: number, payload: JobUpdate) => api.patch<Job>(`/jobs/${id}`, payload),

  remove: (id: number) => api.delete<void>(`/jobs/${id}`),

  /** Parse a pasted description. `persist: false` is a preview with no writes. */
  parse: (payload: { description: string; title?: string | null; persist?: boolean }) =>
    api.post<JobExtraction>('/jobs/parse', payload),

  uploadDescription: (file: File, onProgress?: (percent: number) => void) => {
    const form = new FormData()
    form.append('file', file)
    return upload<Job>('/jobs/upload', form, onProgress)
  },

  candidateCount: (id: number) => api.get<JobCandidateCount>(`/jobs/${id}/candidates`),

  requirements: (id: number) =>
    api.get<Record<string, unknown>>(`/jobs/${id}/requirements`),
}