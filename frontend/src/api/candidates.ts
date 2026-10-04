import { api, upload } from './client'
import type {
  BulkUploadResponse,
  Candidate,
  CandidateCreate,
  CandidateSummary,
  CandidateUpdate,
  Page,
  ResumeUploadResult,
} from './types'

export interface CandidateListParams {
  search?: string
  location?: string
  current_title?: string
  min_experience?: number
  max_experience?: number
  education?: string
  certification?: string
  shortlisted_only?: boolean
  skills?: string[]
  page?: number
  page_size?: number
}

export const candidatesApi = {
  list: (params: CandidateListParams = {}) =>
    api.get<Page<CandidateSummary>>('/candidates', { params }),

  get: (id: number) => api.get<Candidate>(`/candidates/${id}`),

  create: (payload: CandidateCreate) => api.post<Candidate>('/candidates', payload),

  update: (id: number, payload: CandidateUpdate) =>
    api.patch<Candidate>(`/candidates/${id}`, payload),

  remove: (id: number, hard = false) =>
    api.delete<void>(`/candidates/${id}`, { params: { hard } }),

  setShortlist: (id: number, shortlisted: boolean) =>
    api.post<{ id: number; shortlisted: boolean }>(`/candidates/${id}/shortlist`, {
      shortlisted,
    }),

  reparse: (id: number) => api.post<ResumeUploadResult>(`/candidates/${id}/reparse`),

  sections: (id: number) =>
    api.get<Record<string, unknown>>(`/candidates/${id}/sections`),

  resumeUrl: (id: number) => `/api/candidates/${id}/resume`,

  /** Multi-file upload. `onProgress` reports whole-batch percentage. */
  uploadResumes: (files: File[], onProgress?: (percent: number) => void) => {
    const form = new FormData()
    files.forEach((file) => form.append('files', file))
    return upload<BulkUploadResponse>('/candidates/upload', form, onProgress)
  },

  replaceResume: (id: number, file: File, onProgress?: (percent: number) => void) => {
    const form = new FormData()
    form.append('file', file)
    return upload<ResumeUploadResult>(`/candidates/${id}/resume`, form, onProgress)
  },
}