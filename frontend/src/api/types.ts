/**
 * Types mirroring the backend Pydantic schemas.
 *
 * These were written against the generated OpenAPI document, so a field name
 * here that does not exist on the server is a type error rather than a runtime
 * surprise. When the backend schema changes, regenerate from
 * `/openapi.json` and update this file in the same commit.
 */

export type UserRole = 'admin' | 'recruiter' | 'hiring_manager'

export type ProcessingStatus = 'uploaded' | 'parsing' | 'parsed' | 'failed'

export type SkillCategory =
  | 'programming_language'
  | 'framework'
  | 'database'
  | 'cloud'
  | 'ai_ml'
  | 'devops'
  | 'data_science'
  | 'frontend'
  | 'mobile'
  | 'testing'
  | 'soft_skill'
  | 'certification'
  | 'other'

export interface User {
  id: number
  name: string
  email: string
  role: UserRole
  is_active: boolean
  created_at: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface AuthResponse {
  user: User
  tokens: TokenPair
}

export interface Skill {
  id: number
  name: string
  normalized_name: string
  category: SkillCategory
  description: string | null
}

export interface CandidateSkill {
  skill: Skill
  confidence: number
  years_experience: number | null
  is_certified: boolean
  evidence: string[]
}

export interface EmploymentEntry {
  title: string | null
  company: string | null
  start: string | null
  end: string | null
  raw: string | null
}

export interface Candidate {
  id: number
  full_name: string
  email: string | null
  phone: string | null
  location: string | null
  linkedin_url: string | null
  github_url: string | null
  portfolio_url: string | null
  current_title: string | null
  total_experience_years: number
  relevant_experience_years: number
  highest_degree: string | null
  degree_field: string | null
  institution: string | null
  graduation_year: number | null
  certifications: string[]
  previous_positions: string[]
  employment_history: EmploymentEntry[]
  status: ProcessingStatus
  processing_error: string | null
  shortlisted: boolean
  original_filename: string | null
  created_at: string
  skills: CandidateSkill[]
}

export interface CandidateSummary {
  id: number
  full_name: string
  email: string | null
  location: string | null
  current_title: string | null
  total_experience_years: number
  highest_degree: string | null
  shortlisted: boolean
  status: ProcessingStatus
  created_at: string
  top_skills: string[]
  overall_score: number | null
  rank: number | null
}

export interface CandidateCreate {
  full_name: string
  email?: string | null
  phone?: string | null
  location?: string | null
  linkedin_url?: string | null
  github_url?: string | null
  portfolio_url?: string | null
  current_title?: string | null
  total_experience_years?: number
  education_required?: string | null
  skills?: string[]
  raw_text?: string | null
}

export type CandidateUpdate = Partial<
  Omit<CandidateCreate, 'skills' | 'raw_text' | 'education_required'>
> & {
  degree_field?: string | null
  institution?: string | null
  graduation_year?: number | null
  shortlisted?: boolean | null
}

export interface Page<T> {
  total: number
  page: number
  page_size: number
  pages: number
  results: T[]
}

export interface ResumeUploadResult {
  candidate: Candidate
  warnings: string[]
}

export interface BulkUploadResponse {
  total: number
  succeeded: number
  failed: number
  results: ResumeUploadResult[]
  errors: Record<string, string>[]
  batch_id: number | null
}

export interface Job {
  id: number
  title: string
  department: string | null
  location: string | null
  employment_type: string | null
  description: string
  experience_required_years: number
  education_required: string | null
  required_skills: string[]
  preferred_skills: string[]
  certifications: string[]
  responsibilities: string[]
  soft_skills: string[]
  source_file: string | null
  is_active: boolean
  created_by: number | null
  created_at: string
  updated_at: string
}

export interface JobCreate {
  title: string
  description?: string
  department?: string | null
  location?: string | null
  employment_type?: string | null
  required_skills?: string[]
  preferred_skills?: string[]
  certifications?: string[]
  responsibilities?: string[]
  soft_skills?: string[]
  experience_required_years?: number
  education_required?: string | null
  weight_profile?: string | null
}

export type JobUpdate = Partial<JobCreate> & { is_active?: boolean | null }

export interface JobExtraction {
  job_id: number | null
  title: string | null
  department: string | null
  location: string | null
  employment_type: string | null
  experience_required_years: number
  education_required: string | null
  required_skills: string[]
  preferred_skills: string[]
  certifications: string[]
  responsibilities: string[]
  soft_skills: string[]
  raw_text: string
}

export interface ScoringWeights {
  required_skills: number
  experience: number
  education: number
  preferred_skills: number
  semantic: number
  certifications: number
}

export interface MatchEvidence {
  component: string
  label: string
  detail: string
  status: string
  weight: number
  snippet: string | null
}

export interface MatchExplanation {
  summary: string
  strong_matches: string[]
  preferred_matches: string[]
  missing_required: string[]
  weak_signals: string[]
  experience_note: string | null
  education_note: string | null
  certification_note: string | null
  semantic_note: string | null
  recommendation: string
}

export interface MatchResult {
  id: number
  job_id: number
  candidate_id: number
  required_skills_score: number
  experience_score: number
  education_score: number
  preferred_skills_score: number
  semantic_score: number
  certifications_score: number
  overall_score: number
  weights: ScoringWeights
  explanation: MatchExplanation | null
  evidence: MatchEvidence[]
  created_at: string
}

export interface RankedCandidate {
  rank: number
  candidate: CandidateSummary
  match: MatchResult
}

export interface Ranking {
  job_id: number
  job_title: string
  total_candidates: number
  sort_by: string
  weights: ScoringWeights
  results: RankedCandidate[]
  disclaimer: string
}

export interface AnalyzeRequest {
  job_id: number
  candidate_ids?: number[] | null
  limit?: number | null
  weights?: ScoringWeights | null
  weight_profile?: string | null
  force?: boolean
}

export interface AnalyzeResponse {
  job_id: number
  analyzed: number
  skipped: number
  failed: number
  duration_ms: number
  top_candidates: RankedCandidate[]
}

export interface ComparisonRow {
  criterion: string
  label: string
  values: Record<string, number>
  best: string[]
  note: string | null
}

export interface Comparison {
  job_id: number | null
  criteria: string[]
  columns: string[]
  rows: ComparisonRow[]
  skills_matrix: Record<string, Record<string, boolean>>
  disclaimer: string
}

export interface TablePayload {
  title: string | null
  columns: string[]
  rows: unknown[][]
}

export interface AssistantSource {
  candidate_id?: number
  job_id?: number
  snippet?: string
  [key: string]: unknown
}

export interface AssistantResponse {
  answer: string
  conversation_id: number | null
  tables: TablePayload[]
  sources: AssistantSource[]
  intent: string | null
  mode: string
  suggestions: string[]
}

export interface DashboardStats {
  total_candidates: number
  active_jobs: number
  candidates_processed: number
  average_match_score: number
  shortlisted_candidates: number
  total_skills: number
  failed_processing: number
}

export interface JobCandidateCount {
  job_id: number
  job_title: string
  candidate_count: number
  average_score: number
}

export interface ScoreBucket {
  bucket: string
  count: number
}

export interface SkillCount {
  skill: string
  normalized_name: string
  category: string
  count: number
}

export interface MissingSkillCount {
  job_id: number
  skill: string
  missing_count: number
}

export interface RecentCandidate {
  id: number
  full_name: string
  current_title: string | null
  status: ProcessingStatus
  created_at: string
  original_filename: string | null
}

export interface Dashboard {
  stats: DashboardStats
  candidates_per_job: JobCandidateCount[]
  score_distribution: ScoreBucket[]
  top_skills: SkillCount[]
  most_missing_skills: MissingSkillCount[]
  recent_candidates: RecentCandidate[]
}

export interface WeightProfile {
  id: number
  name: string
  description: string | null
  weights: ScoringWeights
  is_default: boolean
  created_at: string
}

export interface Health {
  status: string
  version: string
  environment: string
  database: string
  embedding_provider: string
  llm_provider: string
}

export interface AuditLog {
  id: number
  action: string
  user_id: number | null
  entity_type: string | null
  entity_id: number | null
  detail: Record<string, unknown> | null
  ip_address: string | null
  user_agent: string | null
  created_at: string
}

export interface StorageInfo {
  upload_dir: string
  max_file_size_mb: number
  allowed_extensions: string[]
  total_files: number
  total_size_mb: number
  retention_days: number
}

export interface UserCreate {
  name: string
  email: string
  password: string
  role?: UserRole
}

export interface ValidationErrorItem {
  loc: string[]
  msg: string
}

export interface PasswordChange {
  current_password: string
  new_password: string
}