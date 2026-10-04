import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, tokenStore, userStore } from '@/api/client'

const axiosMock = vi.hoisted(() => ({
  create: vi.fn(),
  post: vi.fn(),
  isAxiosError: (value: unknown) => Boolean((value as { isAxiosError?: boolean })?.isAxiosError),
}))

vi.mock('axios', () => {
  const instance = {
    interceptors: { request: { use: vi.fn() }, response: { use: vi.fn() } },
    request: vi.fn(),
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  }
  axiosMock.create.mockReturnValue(instance)
  return { default: { create: axiosMock.create, post: axiosMock.post, isAxiosError: axiosMock.isAxiosError } }
})

describe('ApiError', () => {
  it('exposes field errors keyed by backend field name', () => {
    const error = new ApiError('Request validation failed.', 422, [
      { loc: ['body', 'email'], msg: 'Enter a valid email address' },
      { loc: ['body', 'password'], msg: 'Password is too short' },
    ])

    expect(error.status).toBe(422)
    expect(error.fieldErrors()).toEqual({
      email: 'Enter a valid email address',
      password: 'Password is too short',
    })
  })

  it('keeps the first message when a field fails twice', () => {
    const error = new ApiError('Request validation failed.', 422, [
      { loc: ['body', 'email'], msg: 'First' },
      { loc: ['body', 'email'], msg: 'Second' },
    ])
    expect(error.fieldErrors().email).toBe('First')
  })
})

describe('tokenStore', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('round-trips a token pair', () => {
    tokenStore.save({ access_token: 'a', refresh_token: 'r', token_type: 'bearer', expires_in: 900 })
    expect(tokenStore.access).toBe('a')
    expect(tokenStore.refresh).toBe('r')
  })

  it('clears every credential together, including the cached user', () => {
    tokenStore.save({ access_token: 'a', refresh_token: 'r', token_type: 'bearer', expires_in: 900 })
    userStore.set({ id: 1, name: 'Ada' })
    tokenStore.clear()

    expect(tokenStore.access).toBeNull()
    expect(tokenStore.refresh).toBeNull()
    expect(userStore.get()).toBeNull()
  })
})