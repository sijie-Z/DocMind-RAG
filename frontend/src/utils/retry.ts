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
        // Retry-After 可能是 HTTP-date（如 "Fri, 31 Dec 1999 23:59:59 GMT"）或秒数：
        // 先 Date.parse 解析 HTTP-date（直接得毫秒），再 parseInt 秒数，都失败则回退默认延迟
        let actualDelay = delay
        if (retryAfter) {
          const httpDateMs = Date.parse(retryAfter)
          const seconds = parseInt(retryAfter, 10)
          if (!Number.isNaN(httpDateMs)) {
            actualDelay = httpDateMs
          } else if (!Number.isNaN(seconds) && seconds >= 0) {
            actualDelay = seconds * 1000
          } else {
            actualDelay = 1000
          }
        }
        // 最终延迟必须是有限非负数字，否则回退默认延迟
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
