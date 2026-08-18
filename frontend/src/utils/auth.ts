import Cookies from 'js-cookie'

const TOKEN_KEY = 'docmind_token'
const LEGACY_TOKEN_KEY = 'paicongming_token'
const EXPIRES_KEY = 'docmind_token_expires'
const LEGACY_EXPIRES_KEY = 'paicongming_token_expires'
const REFRESH_TOKEN_KEY = 'docmind_refresh_token'
const LEGACY_REFRESH_TOKEN_KEY = 'paicongming_refresh_token'

export function getToken(): string | undefined {
  const raw = Cookies.get(TOKEN_KEY) ?? localStorage.getItem(TOKEN_KEY)
    ?? Cookies.get(LEGACY_TOKEN_KEY) ?? localStorage.getItem(LEGACY_TOKEN_KEY) ?? undefined
  // 兼容历史脏数据：读取时同样 trim 并去除首尾引号
  return raw ? raw.trim().replace(/^["']|["']$/g, '') : undefined
}

export function setToken(token: string, expiresIn: number): void {
  // 出口统一清洗：trim 并去除首尾引号，保证下游（axios/ws/sse）拿到干净 token
  const cleanToken = token.trim().replace(/^["']|["']$/g, '')
  const expires = new Date(Date.now() + expiresIn * 1000)
  Cookies.set(TOKEN_KEY, cleanToken, { expires })
  localStorage.setItem(TOKEN_KEY, cleanToken)
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
