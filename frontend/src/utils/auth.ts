import Cookies from 'js-cookie'

const TOKEN_KEY = 'docmind_token'
const LEGACY_TOKEN_KEY = 'paicongming_token'
const EXPIRES_KEY = 'docmind_token_expires'
const LEGACY_EXPIRES_KEY = 'paicongming_token_expires'
const REFRESH_TOKEN_KEY = 'docmind_refresh_token'
const LEGACY_REFRESH_TOKEN_KEY = 'paicongming_refresh_token'

export function getToken(): string | undefined {
  return Cookies.get(TOKEN_KEY) ?? localStorage.getItem(TOKEN_KEY)
    ?? Cookies.get(LEGACY_TOKEN_KEY) ?? localStorage.getItem(LEGACY_TOKEN_KEY) ?? undefined
}

export function setToken(token: string, expiresIn: number): void {
  const expires = new Date(Date.now() + expiresIn * 1000)
  Cookies.set(TOKEN_KEY, token, { expires })
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(EXPIRES_KEY, expires.getTime().toString())
}

export function getRefreshToken(): string | undefined {
  return (
    localStorage.getItem(REFRESH_TOKEN_KEY) ??
    localStorage.getItem(LEGACY_REFRESH_TOKEN_KEY) ??
    undefined
  )
}

export function setRefreshToken(refreshToken: string): void {
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
}

export function removeToken(): void {
  Cookies.remove(TOKEN_KEY)
  Cookies.remove(LEGACY_TOKEN_KEY)
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(EXPIRES_KEY)
  localStorage.removeItem(LEGACY_TOKEN_KEY)
  localStorage.removeItem(LEGACY_EXPIRES_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  localStorage.removeItem(LEGACY_REFRESH_TOKEN_KEY)
}

export function isTokenExpired(): boolean {
  const expires = localStorage.getItem(EXPIRES_KEY) ?? localStorage.getItem(LEGACY_EXPIRES_KEY)
  if (!expires) return true
  return Date.now() > parseInt(expires, 10)
}
