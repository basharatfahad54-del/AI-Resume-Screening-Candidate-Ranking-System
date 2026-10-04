/**
 * Centralised HTTP client.
 *
 * Responsibilities kept here so no screen has to think about them:
 *  - attach the access token,
 *  - transparently refresh it once on a 401 and replay the original request,
 *  - surface backend errors as a single `ApiError` shape,
 *  - redirect to /login when the session cannot be recovered.
 *
 * Tokens live in localStorage. That is a deliberate, documented trade-off: it
 * survives a page reload and works across tabs, but it is readable by any
 * script running on the origin, so the access token is kept short-lived and the
 * refresh token is the only durable credential.
 */
import axios, {
  AxiosError,
  type AxiosInstance,
  type AxiosRequestConfig,
  type InternalAxiosRequestConfig,
} from 'axios'

import type { TokenPair, ValidationErrorItem } from './types'

const ACCESS_KEY = 'talentmatch.access_token'
const REFRESH_KEY = 'talentmatch.refresh_token'
const USER_KEY = 'talentmatch.user'

/** Base URL. Empty string means "same origin", which is what the dev proxy and nginx both provide. */
export const API_BASE_URL = (import.meta.env.VITE_API_URL ?? '').replace(/\/$/, '')

export const tokenStore = {
  get access(): string | null {
    return localStorage.getItem(ACCESS_KEY)
  },
  get refresh(): string | null {
    return localStorage.getItem(REFRESH_KEY)
  },
  save(tokens: TokenPair): void {
    localStorage.setItem(ACCESS_KEY, tokens.access_token)
    localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
  },
  clear(): void {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
    localStorage.removeItem(USER_KEY)
  },
}

export const userStore = {
  get(): string | null {
    return localStorage.getItem(USER_KEY)
  },
  set(user: unknown): void {
    localStorage.setItem(USER_KEY, JSON.stringify(user))
  },
}

export class ApiError extends Error {
  readonly status: number
  readonly errors: ValidationErrorItem[]
  readonly requestId: string | null

  constructor(
    message: string,
    status: number,
    errors: ValidationErrorItem[] = [],
    requestId: string | null = null,
  ) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.errors = errors
    this.requestId = requestId
  }

  /** Field-keyed messages, ready to attach to form inputs. */
  fieldErrors(): Record<string, string> {
    const result: Record<string, string> = {}
    for (const item of this.errors) {
      const field = item.loc.filter((part) => part !== 'body').join('.')
      if (field && !(field in result)) {
        result[field] = item.msg
      }
    }
    return result
  }
}

const http: AxiosInstance = axios.create({
  baseURL: `${API_BASE_URL}/api`,
  timeout: 120_000,
  headers: { 'Content-Type': 'application/json' },
})

http.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  const token = tokenStore.access
  if (token) {
    config.headers.set('Authorization', `Bearer ${token}`)
  }
  return config
})

let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const refreshToken = tokenStore.refresh
  if (!refreshToken) return null

  // Collapse parallel 401s into a single refresh call; otherwise a screen that
  // fires several requests at once rotates the token several times and the
  // earlier responses invalidate each other.
  if (!refreshPromise) {
    refreshPromise = axios
      .post<{ tokens: TokenPair }>(
        `${API_BASE_URL}/api/auth/refresh`,
        { refresh_token: refreshToken },
        { headers: { 'Content-Type': 'application/json' } },
      )
      .then((response) => {
        tokenStore.save(response.data.tokens)
        return response.data.tokens.access_token
      })
      .catch(() => {
        tokenStore.clear()
        return null
      })
      .finally(() => {
        refreshPromise = null
      })
  }
  return refreshPromise
}

function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error
  if (axios.isAxiosError(error)) {
    const axiosError = error as AxiosError<{
      detail?: string
      errors?: ValidationErrorItem[]
      request_id?: string
    }>
    const status = axiosError.response?.status ?? 0
    const data = axiosError.response?.data
    const detail =
      typeof data?.detail === 'string'
        ? data.detail
        : axiosError.code === 'ECONNABORTED'
          ? 'The request timed out. The server may still be processing it.'
          : axiosError.message || 'Network error'
    return new ApiError(detail, status, data?.errors ?? [], data?.request_id ?? null)
  }
  return new ApiError(error instanceof Error ? error.message : 'Unexpected error', 0)
}

http.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const original = error.config as (AxiosRequestConfig & { _retried?: boolean }) | undefined
    const status = error.response?.status

    if (status === 401 && original && !original._retried && !original.url?.includes('/auth/')) {
      original._retried = true
      const token = await refreshAccessToken()
      if (token) {
        original.headers = { ...original.headers, Authorization: `Bearer ${token}` }
        return http.request(original)
      }
      // Refresh failed: the session is genuinely over.
      if (!window.location.pathname.startsWith('/login')) {
        window.location.assign('/login')
      }
    }
    return Promise.reject(toApiError(error))
  },
)

export async function request<T>(
  config: AxiosRequestConfig,
  options: { raw?: boolean } = {},
): Promise<T> {
  try {
    const response = await http.request<T>(config)
    return options.raw ? (response as unknown as T) : response.data
  } catch (error) {
    throw toApiError(error)
  }
}

export const api = {
  get: <T>(url: string, config?: AxiosRequestConfig) => request<T>({ ...config, method: 'GET', url }),
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'POST', url, data }),
  patch: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'PATCH', url, data }),
  delete: <T>(url: string, config?: AxiosRequestConfig) =>
    request<T>({ ...config, method: 'DELETE', url }),
}

/** Multipart upload with progress reporting. Never sets Content-Type by hand. */
export async function upload<T>(
  url: string,
  form: FormData,
  onProgress?: (percent: number) => void,
  method: 'POST' | 'PATCH' = 'POST',
): Promise<T> {
  try {
    const response = await http.request<T>({
      method,
      url,
      data: form,
      headers: { 'Content-Type': 'multipart/form-data' },
      onUploadProgress: (event) => {
        if (onProgress && event.total) {
          onProgress(Math.round((event.loaded / event.total) * 100))
        }
      },
    })
    return response.data
  } catch (error) {
    throw toApiError(error)
  }
}

/** Trigger a browser download for a blob response (CSV/JSON export). */
export async function download(
  url: string,
  data: unknown,
  fallbackName: string,
): Promise<void> {
  try {
    const response = await http.request<Blob>({ method: 'POST', url, data, responseType: 'blob' })
    const disposition = response.headers['content-disposition'] ?? ''
    const match = /filename="?([^";]+)"?/i.exec(disposition)
    const blobUrl = URL.createObjectURL(response.data)
    const anchor = document.createElement('a')
    anchor.href = blobUrl
    anchor.download = match?.[1] ?? fallbackName
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(blobUrl)
  } catch (error) {
    throw toApiError(error)
  }
}