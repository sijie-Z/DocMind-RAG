/**
 * issue #289：sseService 传输层回归 + G4/G8 钉桩。
 *
 * - 此前 #253 把 run_report/execution_context/retry_hint 放进了运行时白名单，
 *   但（直到本次）聊天页没有任何 handler；本文件用**真实 sseService**（仅
 *   mock fetch 的 ReadableStream）验证命名事件帧确实能抵达注册的 handler，
 *   作为「传输放行」的回归锚点。
 * - G8：SSEEventType 别名与运行时白名单收敛为单一 SSE_EVENT_TYPES 后，
 *   `on('run_report', ...)` 这类此前 TS 不允许的注册现在合法（vue-tsc 把关）。
 * - G4 生产者侧：429 退避时 emit('retry')，payload 含 attempt/maxRetries/waitTime。
 *
 * 变异点：post() 的 includes 判断改坏 → 命名事件用例红；retry emit 删除 →
 * G4 用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { sseService } from '../sseService'
import type { SSEMessage } from '@/types/api'

function makeStreamResponse(chunks: string[]) {
  const encoder = new TextEncoder()
  let i = 0
  return {
    ok: true,
    status: 200,
    headers: { get: () => null },
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]) }
            : { done: true, value: undefined },
        cancel: vi.fn(),
      }),
    },
  }
}

const REGISTERED = [
  'chunk', 'message', 'error', 'thinking', 'tool_call', 'tool_result',
  'tool_error', 'plan_start', 'plan_step', 'plan_complete', 'reflection', 'done',
  'run_report', 'execution_context', 'retry_hint', 'retry', 'debug',
] as const

describe('sseService · 命名事件派发（issue #289，白名单条目回归锚点）', () => {
  beforeEach(() => {
    localStorage.setItem('docmind_token', 'tok')
    sseService.setRetryOptions({ maxRetries: 3, retryDelay: 5 })
    for (const t of REGISTERED) sseService.off(t)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
    for (const t of REGISTERED) sseService.off(t)
  })

  it('此前白名单放行却零消费者的事件，注册 handler 后逐帧抵达', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeStreamResponse([
      'event: run_report\ndata: {"type":"run_report","content":"报告正文"}\n\n',
      'event: execution_context\ndata: {"type":"execution_context","content":{"total_tokens":9}}\n\n',
      'event: retry_hint\ndata: {"type":"retry_hint","content":"retry_action=reset_transport"}\n\n',
      'event: plan_step\ndata: {"type":"plan_step","planStepId":"s1","content":"步骤一"}\n\n',
      'event: reflection\ndata: {"type":"reflection","content":"ok","result":"pass"}\n\n',
      'event: message\ndata: {"type":"message","content":"收尾"}\n\n',
      'event: error\ndata: {"type":"error","content":"炸了"}\n\n',
    ])))

    const got = new Map<string, SSEMessage>()
    for (const t of ['run_report', 'execution_context', 'retry_hint', 'plan_step', 'reflection', 'message', 'error'] as const) {
      sseService.on(t, (data: SSEMessage) => got.set(t, data))
    }

    const ok = await sseService.post('stream', { content: 'x' })
    expect(ok).toBe(true)

    expect(got.get('run_report')?.content).toBe('报告正文')
    expect((got.get('execution_context')?.content as unknown as Record<string, unknown>).total_tokens).toBe(9)
    expect(got.get('retry_hint')?.content).toBe('retry_action=reset_transport')
    expect(got.get('plan_step')?.planStepId).toBe('s1')
    expect(got.get('reflection')?.result).toBe('pass')
    expect(got.get('message')?.content).toBe('收尾')
    expect(got.get('error')?.content).toBe('炸了')
  })

  it('event: 行权威于 data.type（thinking 帧内 type=chunk 仍派发 thinking）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeStreamResponse([
      'event: thinking\ndata: {"type":"chunk","content":"想","thinkingType":"reasoning"}\n\n',
    ])))
    const chunkHandler = vi.fn()
    const thinkingHandler = vi.fn()
    sseService.on('chunk', chunkHandler)
    sseService.on('thinking', thinkingHandler)

    await sseService.post('stream', {})

    expect(thinkingHandler).toHaveBeenCalledTimes(1)
    expect(thinkingHandler.mock.calls[0][0].type).toBe('thinking')
    expect(chunkHandler).not.toHaveBeenCalled()
  })

  it("G4 生产者侧：429 退避 emit('retry')（attempt/maxRetries/waitTime），随后继续成功流", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false, status: 429, headers: { get: () => null } })
      .mockResolvedValueOnce(makeStreamResponse([
        'event: chunk\ndata: {"type":"chunk","content":"补上"}\n\n',
      ]))
    vi.stubGlobal('fetch', fetchMock)

    const retries: SSEMessage[] = []
    const chunks: SSEMessage[] = []
    sseService.on('retry', (d: SSEMessage) => retries.push(d))
    sseService.on('chunk', (d: SSEMessage) => chunks.push(d))

    const ok = await sseService.post('stream', {})
    expect(ok).toBe(true)
    expect(retries).toHaveLength(1)
    expect(retries[0].attempt).toBe(1)
    expect(retries[0].maxRetries).toBe(3)
    expect(typeof retries[0].waitTime).toBe('number')
    expect(chunks).toHaveLength(1)
    expect(chunks[0].content).toBe('补上')
  })
})
