import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'

import { authApi } from '@/api/auth'
import { tokenStore, userStore } from '@/api/client'
import type { AuthResponse, User } from '@/api/types'

interface AuthState {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
  login: (email: string, password: string) => Promise<User>
  register: (payload: {
    name: string
    email: string
    password: string
    role?: string
  }) => Promise<User>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
  setUser: (user: User) => void
}

const AuthContext = createContext<AuthState | null>(null)

function readCachedUser(): User | null {
  const cached = userStore.get()
  if (!cached) return null
  try {
    return JSON.parse(cached) as User
  } catch {
    userStore.set(null)
    localStorage.removeItem('talentmatch.user')
    return null
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<User | null>(() =>
    tokenStore.access ? readCachedUser() : null,
  )
  const [isLoading, setIsLoading] = useState<boolean>(Boolean(tokenStore.access))

  // A cached user is only a rendering optimisation. Confirm it against the
  // server on first paint so a revoked or expired account cannot keep using the
  // UI on the strength of a stale localStorage entry.
  useEffect(() => {
    if (!tokenStore.access) {
      setIsLoading(false)
      return
    }
    let cancelled = false
    authApi
      .me()
      .then((fresh) => {
        if (cancelled) return
        setUserState(fresh)
        userStore.set(fresh)
      })
      .catch(() => {
        if (cancelled) return
        tokenStore.clear()
        setUserState(null)
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const response: AuthResponse = await authApi.login(email, password)
    setUserState(response.user)
    return response.user
  }, [])

  const register = useCallback<AuthState['register']>(async (payload) => {
    const response = await authApi.register(payload)
    setUserState(response.user)
    return response.user
  }, [])

  const logout = useCallback(async () => {
    await authApi.logout()
    setUserState(null)
  }, [])

  const refreshUser = useCallback(async () => {
    const fresh = await authApi.me()
    setUserState(fresh)
    userStore.set(fresh)
  }, [])

  const setUser = useCallback((next: User) => {
    setUserState(next)
    userStore.set(next)
  }, [])

  const value = useMemo<AuthState>(
    () => ({
      user,
      isLoading,
      isAuthenticated: Boolean(user),
      login,
      register,
      logout,
      refreshUser,
      setUser,
    }),
    [user, isLoading, login, register, logout, refreshUser, setUser],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used inside an AuthProvider')
  }
  return context
}