const MAX_DECODE_ROUNDS = 4
const HEX_ESCAPE = /%[0-9A-Fa-f]{2}/
const INVALID_PERCENT = /%(?![0-9A-Fa-f]{2})/
const CONTROL_CHARS = /[\u0000-\u001F\u007F]/
const JWT_SEGMENT = /^[A-Za-z0-9_-]{3,}$/

const APPLICATION_CREDENTIAL_PARAMS = new Set([
  'accesstoken',
  'idtoken',
  'refreshtoken',
  'jwt',
  'authorization',
  'auth',
  'token',
])

/** Decode only for inspection; malformed, over-layered, and control inputs fail closed. */
function decodeForInspection(value: string): string | null {
  let current = value
  let decodedAny = false
  for (let round = 0; round < MAX_DECODE_ROUNDS; round += 1) {
    if (!HEX_ESCAPE.test(current)) {
      // A literal percent produced by decoding %25 is valid; an original bare one is not.
      if (!decodedAny && current.includes('%')) return null
      return current
    }
    try {
      const next = decodeURIComponent(current)
      if (CONTROL_CHARS.test(next)) return null
      decodedAny = true
      if (next === current) return current
      current = next
    } catch {
      return null
    }
  }
  return HEX_ESCAPE.test(current) ? null : current
}

function hasJwtShape(value: string): boolean {
  return value.split(/[\s/?#&=]+/).some((candidate) => {
    const parts = candidate.split('.')
    return parts.length === 3 && parts.every((part) => JWT_SEGMENT.test(part))
  })
}

function hasHostnameJwtShape(hostname: string): boolean {
  // DNS 根点不参与形态判断；返回值仍保留调用方提供的原始 hostname。
  const parts = hostname.replace(/\.+$/, '').split('.')
  if (parts.length !== 3 || !parts.every((part) => JWT_SEGMENT.test(part))) return false
  // Do not confuse normal three-label DNS names with compact JSON-token segments.
  return parts[0].startsWith('eyJ') || parts.every((part) => part.length >= 12)
}

function normalizePathname(pathname: string): string | null {
  const decoded = decodeForInspection(pathname)
  if (decoded === null) return null
  const segments = decoded.replace(/\\/g, '/').split('/')
  const normalized: string[] = []
  for (const segment of segments) {
    if (!segment || segment === '.') continue
    if (segment === '..') {
      normalized.pop()
      continue
    }
    normalized.push(segment)
  }
  return `/${normalized.join('/')}`
}

function isProtectedApiPath(normalizedPath: string): boolean {
  const path = normalizedPath.toLowerCase()
  return path === '/api' || path.startsWith('/api/')
}

function isApplicationCredentialParam(key: string): boolean {
  return APPLICATION_CREDENTIAL_PARAMS.has(key.toLowerCase().replace(/[-_]/g, ''))
}

function hasForbiddenParameters(params: IterableIterator<[string, string]>): boolean {
  for (const [rawKey, rawValue] of params) {
    const key = decodeForInspection(rawKey)
    const value = decodeForInspection(rawValue)
    if (key === null || value === null) return true
    if (isApplicationCredentialParam(key) || hasJwtShape(key) || hasJwtShape(value)) return true
  }
  return false
}

/**
 * Return the original, trimmed URL only when it is an absolute HTTP(S) presigned
 * candidate. Canonical forms are used solely for checks and never for opening or logs.
 */
export function parseTrustedDownloadUrl(raw: string): string | null {
  if (typeof raw !== 'string' || !raw.trim()) return null
  const candidate = raw.trim()
  if (CONTROL_CHARS.test(candidate) || INVALID_PERCENT.test(candidate)) return null

  let parsed: URL
  try {
    parsed = new URL(candidate)
  } catch {
    return null
  }
  if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname) return null
  if (parsed.username || parsed.password) return null

  const normalizedPath = normalizePathname(parsed.pathname)
  const normalizedHostname = decodeForInspection(parsed.hostname)
  const normalizedHash = decodeForInspection(parsed.hash)
  if (normalizedPath === null || normalizedHostname === null || normalizedHash === null) return null
  if (isProtectedApiPath(normalizedPath)) return null

  // Check both original and decoded forms without rewriting the signed URL itself.
  if (hasJwtShape(parsed.pathname) || hasJwtShape(normalizedPath)) return null
  if (hasJwtShape(parsed.hash) || hasJwtShape(normalizedHash)) return null
  if (hasHostnameJwtShape(parsed.hostname) || hasHostnameJwtShape(normalizedHostname)) return null

  const params = [...parsed.searchParams.entries()]
  const hashParams = new URLSearchParams(parsed.hash.replace(/^#/, ''))
  params.push(...hashParams.entries())
  if (hasForbiddenParameters(params[Symbol.iterator]())) return null

  return candidate
}
