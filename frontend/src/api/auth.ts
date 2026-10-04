import { api, tokenStore, userStore } from './client'
import type { AuthResponse, PasswordChange, User } from './types'

export const authApi = {
  async login(email: string, password: string): Promise<AuthResponse> {
    const response = await api.post<AuthResponse>('/auth/login', { email, password })
    tokenStore.save(response.tokens)
    userStore.set(response.user)
    return response
  },

  async register(payload: {
    name: string
    email: string
    password: string
    role?: string
  }): Promise<AuthResponse> {
    const response = await api.post<AuthResponse>('/auth/register', payload)
    tokenStore.save(response.tokens)
    userStore.set(response.user)
    return response
  },

  async logout(): Promise<void> {
    const refresh = tokenStore.refresh
    try {
      // An empty body means "end every session" server-side.
      await api.post('/auth/logout', refresh ? { refresh_token: refresh } : {})
    } catch {
      // A failed logout must never trap the user in a signed-in UI.
    } finally {
      tokenStore.clear()
    }
  },

  me(): Promise<User> {
    return api.get<User>('/auth/me')
  },

  changePassword(payload: PasswordChange): Promise<void> {
    return api.post('/auth/change-password', payload)
  },
}