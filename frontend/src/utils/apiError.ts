/**
 * 后端错误详情提取（axios 错误 → 诚实文案）。
 *
 * 口径与 request.ts 拦截器一致：`data.detail ?? data.message`——兼容两种后端
 * 惯例（AppError 首参进 message、端点用 detail 传文案）。拦截器对 403 等状态
 * 会覆盖通用文案，因此凡要求"后端语义原文"的调用点须带 X-Silent-Error，
 * 在 catch 里用本函数呈现。
 */
export function extractApiError(error: unknown, fallback: string): string {
  const detail = extractBackendDetail(error)
  if (detail) return detail
  return error instanceof Error && error.message ? error.message : fallback
}

/** 后端语义原文（detail ?? message，含 FastAPI 422 数组）；无则 null。 */
function extractBackendDetail(error: unknown): string | null {
  const data = (error as { response?: { data?: { detail?: unknown; message?: unknown } } })?.response?.data
  const raw = data?.detail ?? data?.message
  // FastAPI 422：detail 为 [{msg, loc, ...}] 数组，与拦截器同口径拼接
  if (Array.isArray(raw)) {
    const parts = raw.map((e) =>
      e && typeof e === 'object' && 'msg' in e ? String((e as { msg: unknown }).msg) : String(e),
    )
    const joined = parts.filter(Boolean).join('; ')
    if (joined) return joined
  }
  if (typeof raw === 'string' && raw.trim()) return raw
  // 结构化 detail（如 409 SOURCE_ORIGINAL_UNAVAILABLE）不直接上屏；优先用
  // 后端同时给出的可读 message，避免页面退化成“检查网络/重试”。
  if (raw && typeof raw === 'object' && typeof data?.message === 'string' && data.message.trim()) {
    return data.message
  }
  return null
}

/** 传输层错误（axios reject）：error.message 是框架英文文案，禁止直接上屏。 */
function isTransportError(error: unknown): boolean {
  if (!error || typeof error !== 'object') return false
  const e = error as { isAxiosError?: unknown; response?: unknown; config?: unknown }
  return e.isAxiosError === true || e.response != null || e.config != null
}

/**
 * 页面 catch 统一错误文案（E14/D1）。
 *
 * - 后端语义 detail/message 原文优先（4xx 校验/权限等可读文案）；
 * - 5xx 不展示后端内部细节（与 request.ts 拦截器同口径，防堆栈/内部路径泄露）；
 * - 传输层 axios 自带文案（"Request failed with status code ..."、"Network Error"）
 *   永不上屏——无后端语义时用调用方传入的 i18n 兜底；
 * - 非 axios 的普通 Error（如前端本地校验）仍可透出其 message。
 */
export function getErrorMessage(error: unknown, fallback = ''): string {
  const status = (error as { response?: { status?: number } })?.response?.status
  if (typeof status === 'number' && status >= 500) return fallback
  const detail = extractBackendDetail(error)
  if (detail) return detail.trim()
  if (isTransportError(error)) return fallback
  return error instanceof Error && error.message ? error.message : fallback
}
