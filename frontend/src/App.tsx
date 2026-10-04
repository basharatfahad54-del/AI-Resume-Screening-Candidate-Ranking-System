import { Navigate, Route, Routes } from 'react-router-dom'
import type { ReactElement } from 'react'

import Layout from '@/components/Layout'
import { LoadingBlock } from '@/components/Feedback'
import { useAuth } from '@/context/AuthContext'

import AssistantPage from '@/pages/AssistantPage'
import AdminPage from '@/pages/AdminPage'
import CandidateDetailPage from '@/pages/CandidateDetailPage'
import CandidatesPage from '@/pages/CandidatesPage'
import ComparePage from '@/pages/ComparePage'
import DashboardPage from '@/pages/DashboardPage'
import JobCreatePage from '@/pages/JobCreatePage'
import JobDetailPage from '@/pages/JobDetailPage'
import JobsPage from '@/pages/JobsPage'
import LoginPage from '@/pages/LoginPage'
import NotFoundPage from '@/pages/NotFoundPage'
import RegisterPage from '@/pages/RegisterPage'
import SettingsPage from '@/pages/SettingsPage'

function RequireAuth({ children }: { children: ReactElement }) {
  const { isAuthenticated, isLoading } = useAuth()

  // Waiting matters: redirecting during the /auth/me check would bounce a
  // signed-in user to /login on every hard refresh.
  if (isLoading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <LoadingBlock label="Restoring your session" />
      </div>
    )
  }
  return isAuthenticated ? children : <Navigate to="/login" replace />
}

function RequireAdmin({ children }: { children: ReactElement }) {
  const { user } = useAuth()
  return user?.role === 'admin' ? children : <Navigate to="/" replace />
}

function RedirectIfAuthenticated({ children }: { children: ReactElement }) {
  const { isAuthenticated, isLoading } = useAuth()
  if (isLoading) {
    return (
      <div className="grid min-h-screen place-items-center">
        <LoadingBlock label="Checking your session" />
      </div>
    )
  }
  return isAuthenticated ? <Navigate to="/" replace /> : children
}

export default function App() {
  return (
    <Routes>
      <Route
        path="/login"
        element={
          <RedirectIfAuthenticated>
            <LoginPage />
          </RedirectIfAuthenticated>
        }
      />
      <Route
        path="/register"
        element={
          <RedirectIfAuthenticated>
            <RegisterPage />
          </RedirectIfAuthenticated>
        }
      />

      <Route
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<DashboardPage />} />
        <Route path="jobs" element={<JobsPage />} />
        <Route path="jobs/new" element={<JobCreatePage />} />
        <Route path="jobs/:jobId" element={<JobDetailPage />} />
        <Route path="candidates" element={<CandidatesPage />} />
        <Route path="candidates/:candidateId" element={<CandidateDetailPage />} />
        <Route path="compare" element={<ComparePage />} />
        <Route path="assistant" element={<AssistantPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route
          path="admin"
          element={
            <RequireAdmin>
              <AdminPage />
            </RequireAdmin>
          }
        />
      </Route>

      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}