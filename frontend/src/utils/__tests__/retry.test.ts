import { describe, it, expect, vi } from 'vitest'
import { withRetry } from '../retry'

describe('withRetry', () => {
  it('returns result on first success', async () => {
    const fn = vi.fn().mockResolvedValue('ok')
    const result = await withRetry(fn)
    expect(result).toBe('ok')
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('retries on retryable error and succeeds', async () => {
    const fn = vi.fn()
      .mockRejectedValueOnce({ status: 429 })
      .mockResolvedValue('ok')

    const result = await withRetry(fn, { retryDelay: 1 })
    expect(result).toBe('ok')
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('throws after max retries exhausted', async () => {
    const fn = vi.fn().mockRejectedValue({ status: 503 })

    await expect(
      withRetry(fn, { maxRetries: 2, retryDelay: 1 })
    ).rejects.toMatchObject({ status: 503 })
    expect(fn).toHaveBeenCalledTimes(3) // 1 initial + 2 retries
  })

  it('does not retry non-retryable errors', async () => {
    const fn = vi.fn().mockRejectedValue({ status: 400 })

    await expect(
      withRetry(fn, { retryDelay: 1 })
    ).rejects.toMatchObject({ status: 400 })
    expect(fn).toHaveBeenCalledTimes(1)
  })

  it('calls onRetry callback', async () => {
    const onRetry = vi.fn()
    const fn = vi.fn()
      .mockRejectedValueOnce({ status: 504 })
      .mockResolvedValue('ok')

    await withRetry(fn, { retryDelay: 1, onRetry })
    expect(onRetry).toHaveBeenCalledTimes(1)
    expect(onRetry).toHaveBeenCalledWith({ status: 504 }, 1)
  })

  it('uses custom retry condition', async () => {
    const fn = vi.fn()
      .mockRejectedValueOnce(new Error('custom'))
      .mockResolvedValue('ok')

    const result = await withRetry(fn, {
      retryDelay: 1,
      retryCondition: (err) => err.message === 'custom',
    })
    expect(result).toBe('ok')
    expect(fn).toHaveBeenCalledTimes(2)
  })

  it('respects retry-after header', async () => {
    const fn = vi.fn()
      .mockRejectedValueOnce({
        status: 429,
        response: { headers: { 'retry-after': '0' } },
      })
      .mockResolvedValue('ok')

    const result = await withRetry(fn, { retryDelay: 10000 })
    expect(result).toBe('ok')
  })

  it('handles zero max retries (no retries)', async () => {
    const fn = vi.fn().mockRejectedValue({ status: 503 })

    await expect(
      withRetry(fn, { maxRetries: 0, retryDelay: 1 })
    ).rejects.toMatchObject({ status: 503 })
    expect(fn).toHaveBeenCalledTimes(1)
  })
})
