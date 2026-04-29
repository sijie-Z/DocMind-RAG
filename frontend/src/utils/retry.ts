interface RetryOptions {
  maxRetries?: number
  retryDelay?: number
  retryCondition?: (error: any) => boolean
  onRetry?: (error: any, attempt: number) => void
}

const DEFAULT_RETRY_OPTIONS: Required<RetryOptions> = {
  maxRetries: 3,
  retryDelay: 1000,
  retryCondition: (error: any) => {
    if (!error) return false
    const status = error.status || error.response?.status
    return status === 429 || status === 503 || status === 504 || status === 408 || status === 0
  },
  onRetry: () => {}
}

export async function withRetry<T>(
  fn: () => Promise<T>,
  options: RetryOptions = {}
): Promise<T> {
  const opts = { ...DEFAULT_RETRY_OPTIONS, ...options }
  let lastError: any

  for (let attempt = 1; attempt <= opts.maxRetries + 1; attempt++) {
    try {
      return await fn()
    } catch (error: any) {
      lastError = error

      if (attempt > opts.maxRetries) {
        break
      }

      if (opts.retryCondition(error)) {
        opts.onRetry(error, attempt)

        const delay = opts.retryDelay * Math.pow(2, attempt - 1)

        const retryAfter = error.headers?.['retry-after'] || error.response?.headers?.['retry-after']
        const actualDelay = retryAfter ? parseInt(retryAfter) * 1000 : delay

        await new Promise(resolve => setTimeout(resolve, actualDelay))
        continue
      }

      throw error
    }
  }

  throw lastError
}

export class RetryableFetch {
  constructor(private options: RetryOptions = {}) {}

  async post(url: string, data: any, headers: Record<string, string> = {}): Promise<Response> {
    return withRetry(
      () => fetch(url, {
        method: 'POST',
        headers,
        body: JSON.stringify(data)
      }),
      this.options
    )
  }
}
