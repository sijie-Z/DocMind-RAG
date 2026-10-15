/**
 * MAJOR（评审回炉）：decidePlanApproval **直测**——只 mock 传输层（global fetch），
 * 真 agentApi 代码全走：请求形状（URL/方法/头/体）+ 两种响应形态解析
 * （SSE 续跑流 / 终态 JSON rejected）+ 422 错误详情（数组 detail 归一）。
 *
 * 评审背景：此前新测试整体 mock @/api/agent，decidePlanApproval 零直测——
 * SSE/JSON 分支、X-Silent-Error 头、错误详情解包全被替身掩盖（本文件补上）。
 *
 * 变异点：
 * - 请求体漏掉 edited_steps/note → edit/reject 用例红；
 * - 把 JSON 响应误当 SSE 消费（或反之）→ 终态用例红；
 * - 422 不读 detail → detail 断言红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { agentApi } from '../agent'

/** 传输层替身：仅形状模拟 fetch Response（不依赖 jsdom 的 undici 细节）。 */
function stubResponse(bodyText: string, contentType: string, status = 200) {
  const encoder = new TextEncoder()
  let consumed = false
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: {
      get: (k: string) => (k.toLowerCase() === 'content-type' ? contentType : null),
    },
    json: async () => JSON.parse(bodyText),
    body: {
      getReader: () => ({
        read: async () => {
          if (consumed) return { done: true, value: undefined }
          consumed = true
          return { done: false, value: encoder.encode(bodyText) }
        },
      }),
    },
  } as unknown as Response
}

function sseBody(events: object[]): string {
  return events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('')
}

describe('agentApi.decidePlanApproval（真实 api 链，只 mock fetch）', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('approve：POST 计划审批端点 + 消费 SSE 续跑流', async () => {
    fetchMock.mockResolvedValue(
      stubResponse(
        sseBody([{ type: 'done', content: '', task_id: 't1' }]),
        'text/event-stream',
      ),
    )
    const events: any[] = []
    const outcome = await agentApi.decidePlanApproval('t1', { decision: 'approve' }, (e) => events.push(e))

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [url, init] = fetchMock.mock.calls[0]
    expect(String(url)).toMatch(/\/agent\/tasks\/t1\/plan-approval$/)
    expect(init.method).toBe('POST')
    expect(init.headers['Content-Type']).toBe('application/json')
    expect(init.headers['X-Silent-Error']).toBe('1')
    expect(JSON.parse(init.body)).toEqual({ decision: 'approve' })
    expect(events).toEqual([expect.objectContaining({ type: 'done', task_id: 't1' })])
    expect(outcome.streamed).toBe(true)
    if (outcome.streamed) expect(typeof outcome.abort).toBe('function')
  })

  it('edit：edited_steps 原样进请求体（trim 后的负载由组件保证）', async () => {
    fetchMock.mockResolvedValue(
      stubResponse(
        sseBody([{ type: 'done', content: '' }]),
        'text/event-stream',
      ),
    )
    const edited = [
      { id: 's1', description: '人类改过的第一步', dependencies: [], tool_hint: null },
    ]
    const outcome = await agentApi.decidePlanApproval(
      't2',
      { decision: 'edit', edited_steps: edited },
      () => {},
    )
    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(init.body)).toEqual({ decision: 'edit', edited_steps: edited })
    expect(outcome.streamed).toBe(true)
  })

  it('reject#1：note 进请求体 + SSE 流内可再次收到 plan_approval_required', async () => {
    fetchMock.mockResolvedValue(
      stubResponse(
        sseBody([
          { type: 'plan_approval_required', content: '计划已生成（1 个步骤），等待审批' },
          { type: 'done', content: '', task_state: 'waiting_plan_approval' },
        ]),
        'text/event-stream',
      ),
    )
    const events: any[] = []
    const outcome = await agentApi.decidePlanApproval(
      't3',
      { decision: 'reject', note: '不要这个思路' },
      (e) => events.push(e),
    )
    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(init.body)).toEqual({ decision: 'reject', note: '不要这个思路' })
    expect(events.map((e) => e.type)).toEqual(['plan_approval_required', 'done'])
    expect(outcome.streamed).toBe(true)
  })

  it('reject#2：JSON 终态 rejected —— streamed=false 且字段解包（无流可续）', async () => {
    fetchMock.mockResolvedValue(
      stubResponse(
        JSON.stringify({
          success: true,
          data: {
            task_id: 't4',
            state: 'rejected',
            decision: 'reject',
            rejections: 2,
            message: '计划已再次被拒绝，重规划机会已用尽，任务终止（rejected）',
          },
        }),
        'application/json',
      ),
    )
    const outcome = await agentApi.decidePlanApproval('t4', { decision: 'reject', note: '还是不行' }, () => {})
    expect(outcome.streamed).toBe(false)
    if (!outcome.streamed) {
      expect(outcome.state).toBe('rejected')
      expect(outcome.rejections).toBe(2)
      expect(outcome.message).toContain('rejected')
    }
  })

  it('422：非 2xx 抛错并解包 FastAPI 数组 detail（调用方据此呈现明确文案）', async () => {
    fetchMock.mockResolvedValue(
      stubResponse(
        JSON.stringify({
          detail: [
            {
              loc: ['body', 'edited_steps', 0, 'description'],
              msg: 'Value error, 每个步骤必须携带非空 description',
              type: 'value_error',
            },
          ],
        }),
        'application/json',
        422,
      ),
    )
    const err: any = await agentApi
      .decidePlanApproval('t5', { decision: 'edit', edited_steps: [] }, () => {})
      .catch((e) => e)
    expect(err).toBeInstanceOf(Error)
    expect(err.status).toBe(422)
    expect(String(err.detail)).toContain('description')
  })
})
