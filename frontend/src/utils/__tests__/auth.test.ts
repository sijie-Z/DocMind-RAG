import { describe, it, expect, beforeEach, vi } from 'vitest'
import { getToken, setToken, removeToken, isTokenExpired, getRefreshToken, setRefreshToken } from '../auth'

// Mock js-cookie
vi.mock('js-cookie', () => {
  const store: Record<string, string> = {}
  return {
    default: {
      get: vi.fn((key: string) => store[key]),
      set: vi.fn((key: string, value: string) => { store[key] = value }),
      remove: vi.fn((key: string) => { delete store[key] }),
    },
  }
})

describe('auth utils', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
  })

  describe('getToken', () => {
    it('returns undefined when no token exists', () => {
      expect(getToken()).toBeUndefined()
    })

    it('returns token from localStorage', () => {
      localStorage.setItem('docmind_token', 'test-token-123')
      expect(getToken()).toBe('test-token-123')
    })

    it('falls back to legacy token key', () => {
      localStorage.setItem('paicongming_token', 'legacy-token')
      expect(getToken()).toBe('legacy-token')
    })
  })

  describe('setToken', () => {
    it('stores token in localStorage', () => {
      setToken('my-token', 3600)
      expect(localStorage.getItem('docmind_token')).toBe('my-token')
    })

    it('stores expiry time', () => {
      const before = Date.now()
      setToken('my-token', 3600)
      const expires = parseInt(localStorage.getItem('docmind_token_expires') || '0', 10)
      expect(expires).toBeGreaterThan(before)
      expect(expires).toBeLessThanOrEqual(before + 3600 * 1000 + 1000)
    })
  })

  describe('removeToken', () => {
    it('clears all token storage', () => {
      localStorage.setItem('docmind_token', 'token')
      localStorage.setItem('docmind_token_expires', '123')
      localStorage.setItem('docmind_refresh_token', 'refresh')
      removeToken()
      expect(localStorage.getItem('docmind_token')).toBeNull()
      expect(localStorage.getItem('docmind_token_expires')).toBeNull()
      expect(localStorage.getItem('docmind_refresh_token')).toBeNull()
    })
  })

  describe('isTokenExpired', () => {
    it('returns true when no expiry stored', () => {
      expect(isTokenExpired()).toBe(true)
    })

    it('returns false when token is still valid', () => {
      const future = Date.now() + 3600 * 1000
      localStorage.setItem('docmind_token_expires', future.toString())
      expect(isTokenExpired()).toBe(false)
    })

    it('returns true when token is expired', () => {
      const past = Date.now() - 1000
      localStorage.setItem('docmind_token_expires', past.toString())
      expect(isTokenExpired()).toBe(true)
    })

    it('checks legacy expiry key', () => {
      const future = Date.now() + 3600 * 1000
      localStorage.setItem('paicongming_token_expires', future.toString())
      expect(isTokenExpired()).toBe(false)
    })
  })

  describe('refreshToken', () => {
    it('stores and retrieves refresh token', () => {
      setRefreshToken('refresh-abc')
      expect(getRefreshToken()).toBe('refresh-abc')
    })

    it('falls back to legacy refresh token', () => {
      localStorage.setItem('paicongming_refresh_token', 'legacy-refresh')
      expect(getRefreshToken()).toBe('legacy-refresh')
    })
  })
})
