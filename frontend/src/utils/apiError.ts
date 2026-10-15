/**
 * 后端错误详情提取（axios 错误 → 诚实文案）。
 *
 * 口径与 request.ts 拦截器一致：`data.detail ?? data.message`——兼容两种后端
 * 惯例（AppError 首参进 message、端点用 detail 传文案）。拦截器对 403 等状态
 * 会覆盖通用文案，因此凡要求"后端语义原文"的调用点须带 X-Silent-Error，
 * 在 catch 里用本函数呈现。
 */
export function extractApiError(error: unknown, fallback: string): string {
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
  return error instanceof Error && error.message ? error.message : fallback
}
