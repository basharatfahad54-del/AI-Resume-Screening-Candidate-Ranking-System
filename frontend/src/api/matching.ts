import { api, download } from './client'
import type {
  AnalyzeRequest,
  AnalyzeResponse,
  Comparison,
  MatchResult,
  Ranking,
  ScoringWeights,
  WeightProfile,
} from './types'

export interface RankingParams {
  sort_by?: 'overall' | 'experience' | 'education' | 'skills'
  shortlisted_only?: boolean
  page?: number
  page_size?: number
}

export const matchingApi = {
  ranking: (jobId: number, params: RankingParams = {}) =>
    api.get<Ranking>(`/jobs/${jobId}/ranking`, { params }),

  analyze: (payload: AnalyzeRequest) => api.post<AnalyzeResponse>('/matches/analyze', payload),

  match: (matchId: number) =>
    api.get<
      MatchResult & { evidence_count: number; disclaimer: string; candidate?: unknown }
    >(`/matches/${matchId}`),

  compare: (payload: { candidate_ids: number[]; job_id?: number | null; criteria?: string[] }) =>
    api.post<Comparison>('/matches/compare', payload),

  exportResults: (payload: {
    job_id: number
    format: 'csv' | 'json'
    include_explanation?: boolean
  }) =>
    download('/matches/export', payload, `talentmatch-export-${payload.job_id}.${payload.format}`),

  listWeightProfiles: () => api.get<WeightProfile[]>('/weights'),

  defaultWeights: () => api.get<ScoringWeights>('/weights/default'),

  createWeightProfile: (payload: {
    name: string
    description?: string | null
    weights: ScoringWeights
    is_default?: boolean
  }) => api.post<WeightProfile>('/weights', payload),

  deleteWeightProfile: (id: number) => api.delete<void>(`/weights/${id}`),
}

export const WEIGHT_COMPONENTS: { key: keyof ScoringWeights; label: string; hint: string }[] = [
  { key: 'required_skills', label: 'Required skills', hint: 'Explicit evidence of the must-have skills' },
  { key: 'experience', label: 'Experience', hint: 'Years of relevant work against the requirement' },
  { key: 'education', label: 'Education', hint: 'Highest qualification against the requirement' },
  { key: 'preferred_skills', label: 'Preferred skills', hint: 'Nice-to-have skills' },
  { key: 'semantic', label: 'Semantic similarity', hint: 'Embedding similarity of resume to job text' },
  { key: 'certifications', label: 'Certifications', hint: 'Relevant certifications held' },
]