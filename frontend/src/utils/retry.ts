// 说明：当前产品代码无消费者，仅由 __tests__/retry.test.ts 覆盖；
// 按「测试资产」保留，请勿误删（如需复活，从生产代码 import 后即可）。
interface RetryOptions {
  maxRetries?: number
  retryDelay?: number
  retryCondition?: (_error: unknown) => boolean
  onRetry?: (_error: unknown, _attempt: number) => void
}

const DEFAULT_RETRY_OPTIONS: Required<RetryOptions> = {
  maxRetries: 3,
  retryDelay: 1000,
  retryCondition: (error: unknown) => {
    if (!error) return false
    const err = error as Record<string, unknown>
    const resp = err.response as Record<string, unknown> | undefined
    const status = (err.status as number) || (resp?.status as number)
    return status === 429 || status === 503 || status === 504 || status === 408 || status === 0
  },
  onRetry: () => {}
}

// setTimeout 只能表达 32 位有符号整数毫秒（2^31 - 1）；更大的值会溢出成 1ms 并产生
// TimeoutOverflowWarning（issue #140 的症状之一），因此解析结果封顶于此
const MAX_TIMEOUT_MS = 2147483647

/**
 * 解析 Retry-After（RFC 9110 §10.2.3），返回应等待的毫秒数（有限、非负）。
 *
 * - delay-seconds（纯数字）：值是「等待秒数」，× 1000 即毫秒
 * - HTTP-date：Date.parse 得到的是绝对时间戳，等待时长 = 距 now 的差值；已过去的日期 → 0
 * - 其余无法解析的值 → 回退 fallbackMs
 *
 * 关键：不能先做 Date.parse 再兜底。ECMAScript 只强制支持 ISO 格式，其余为实现定义，
 * V8 会把 "5" 当作「2001 年」、把 "-1"/"1.5"/"5." 解析成 2001 年前后的日期（issue #140 根因）。
 * 因此必须先按纯数字判定 delay-seconds；无字母的残缺数字（"-1"、"1.5"、"5."）不交给
 * Date.parse，直接回退——否则会经「已过去的时间戳」路径静默变成 0ms 等待。
 */
export function resolveRetryDelay(
  retryAfter: string,
  fallbackMs: number,
  now: number = Date.now()
): number {
  const trimmed = String(retryAfter).trim()
  let actualDelay: number

  if (/^\d+$/.test(trimmed)) {
    actualDelay = parseInt(trimmed, 10) * 1000
  } else if (/[A-Za-z]/.test(trimmed)) {
    // 合法的 HTTP-date（IMF-fixdate / RFC 850 / asctime / ISO）必含字母（月名、星期名或 T/Z）
    const httpDateMs = Date.parse(trimmed)
    actualDelay = Number.isNaN(httpDateMs) ? fallbackMs : Math.max(0, httpDateMs - now)
  } else {
    actualDelay = fallbackMs
  }

  // 封顶：避免超过 setTimeout 可表达的上限而溢出（见 MAX_TIMEOUT_MS 注释）
  actualDelay = Math.min(actualDelay, MAX_TIMEOUT_MS)

  // 末尾兜底：结果必须是有限非负数，否则回退默认延迟
  if (!Number.isFinite(actualDelay) || actualDelay < 0) {
    return fallbackMs
  }
  return actualDelay
}

export async function withRetry<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {}
): Promise<T> {
  const opts = { ...DEFAULT_RETRY_OPTIONS, ...options }
  let lastError: unknown

  for (let attempt = 1; attempt <= opts.maxRetries + 1; attempt++) {
    try {
      return await fn()
    } catch (error: unknown) {
      lastError = error

      if (attempt > opts.maxRetries) {
        break
      }

      if (opts.retryCondition(error)) {
        opts.onRetry(error, attempt)

        const delay = opts.retryDelay * Math.pow(2, attempt - 1)

        const err = error as Record<string, unknown>
        const resp = err.response as Record<string, unknown> | undefined
        const retryAfter = (err.headers as Record<string, string>)?.['retry-after'] || (resp?.headers as Record<string, string>)?.['retry-after']
        // Retry-After（RFC 9110 §10.2.3）优先于指数退避；解析细节见 resolveRetryDelay
        let actualDelay = retryAfter ? resolveRetryDelay(retryAfter, delay) : delay

        // 末尾兜底：最终延迟必须是有限非负数，否则回退默认延迟
        if (!Number.isFinite(actualDelay) || actualDelay < 0) {
          actualDelay = 1000
        }

        await new Promise(resolve => setTimeout(resolve, actualDelay))
        continue
      }

      throw error
    }
  }

  throw lastError
}

export class RetryableFetch {
  constructor(private _options: RetryOptions = {}) {}

  async post(url: string, data: unknown, headers: Record<string, string> = {}): Promise<Response> {
    return withRetry(
      () => fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(data)
      }),
      this._options
    )
  }
}
