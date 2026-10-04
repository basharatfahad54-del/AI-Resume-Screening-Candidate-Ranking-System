import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import App from '@/App'
import { tokenStore } from '@/api/client'
import { AuthProvider } from '@/context/AuthContext'
import { ToastProvider } from '@/context/ToastContext'

const mocks = vi.hoisted(() => ({
  me: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
  dashboard: vi.fn(),
}))

vi.mock('@/api/auth', () => ({
  authApi: {
    me: mocks.me,
    login: mocks.login,
    register: vi.fn(),
    logout: mocks.logout,
    changePassword: vi.fn(),
  },
}))

vi.mock('@/api', () => ({
  dashboardApi: { overview: mocks.dashboard },
  assistantApi: { status: vi.fn(), query: vi.fn() },
  skillsApi: { search: vi.fn(), count: vi.fn() },
}))

vi.mock('@/api/jobs', () => ({
  jobsApi: { list: vi.fn().mockResolvedValue({ total: 0, page: 1, page_size: 12, pages: 1, results: [] }) },
}))
vi.mock('@/api/candidates', () => ({
  candidatesApi: { list: vi.fn().mockResolvedValue({ total: 0, page: 1, page_size: 12, pages: 1, results: [] }) },
}))
vi.mock('@/api/matching', () => ({
  matchingApi: { ranking: vi.fn(), analyze: vi.fn() },
  WEIGHT_COMPONENTS: [],
}))
vi.mock('@/api/admin', () => ({ adminApi: {} }))

const user = {
  id: 1,
  name: 'Ada Lovelace',
  email: 'ada@example.com',
  role: 'recruiter',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
}

function renderAt(path: string) {
  // Same provider stack as main.tsx, so the guards under test behave exactly as
  // they do in the browser.
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ToastProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </ToastProvider>
    </MemoryRouter>,
  )
}

describe('routing guards', () => {
  beforeEach(() => {
    mocks.me.mockReset()
    mocks.login.mockReset()
    mocks.dashboard.mockReset().mockResolvedValue({
      stats: {
        total_candidates: 0,
        active_jobs: 0,
        candidates_processed: 0,
        average_match_score: 0,
        shortlisted_candidates: 0,
        total_skills: 0,
        failed_processing: 0,
      },
      candidates_per_job: [],
      score_distribution: [],
      top_skills: [],
      most_missing_skills: [],
      recent_candidates: [],
    })
  })

  it('redirects an anonymous visitor to the login screen', async () => {
    renderAt('/')
    expect(await screen.findByRole('heading', { name: /sign in/i })).toBeInTheDocument()
  })

  it('lets a signed-in user reach the dashboard', async () => {
    tokenStore.save({ access_token: 'a', refresh_token: 'r', token_type: 'bearer', expires_in: 900 })
    mocks.me.mockResolvedValue(user)

    renderAt('/')
    expect(await screen.findByRole('heading', { name: /dashboard/i })).toBeInTheDocument()
    await waitFor(() => expect(mocks.dashboard).toHaveBeenCalled())
  })

  it('does not offer the admin area to a recruiter', async () => {
    tokenStore.save({ access_token: 'a', refresh_token: 'r', token_type: 'bearer', expires_in: 900 })
    mocks.me.mockResolvedValue(user)

    renderAt('/')
    await screen.findByRole('heading', { name: /dashboard/i })
    expect(screen.queryByRole('link', { name: /^admin$/i })).not.toBeInTheDocument()
  })

  it('shows the admin area to an administrator', async () => {
    tokenStore.save({ access_token: 'a', refresh_token: 'r', token_type: 'bearer', expires_in: 900 })
    mocks.me.mockResolvedValue({ ...user, role: 'admin' })

    renderAt('/')
    await screen.findByRole('heading', { name: /dashboard/i })
    expect(await screen.findByRole('link', { name: /^admin$/i })).toBeInTheDocument()
  })
})

describe('login form', () => {
  it('surfaces a friendly message for bad credentials', async () => {
    const user_ = userEvent.setup()
    renderAt('/login')
    mocks.login.mockRejectedValue(new Error('Email or password is incorrect.'))

    await user_.type(await screen.findByLabelText(/work email/i), 'ada@example.com')
    await user_.type(screen.getByLabelText(/^password/i), 'wrong-password')
    await user_.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/email or password is incorrect/i)
  })
})