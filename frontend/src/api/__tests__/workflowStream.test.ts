/**
 * issue #289 第 2 条：workflow SSE 预埋断链（接线前置件）。
 *
 * 修复前 executeWorkflowStream 三重断点：EventSource 的 GET 打 POST 路由、
 * 无法携带 Authorization、`onmessage` 只收无 `event:` 名的消息（而端点全部
 * 是命名事件）→ 一个都到不了回调。现改为 fetch + ReadableStream（与
 * sseService 的既有 fetch-SSE 模式对齐）+ 纯函数帧解析器。
 *
 * 变异点：解析器 event:/data: 分支或分帧缓冲改坏 → 对应用例红；
 * fetch 改回 GET / 去掉 Authorization → 请求契约用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createWorkflowSSEFrameParser, executeWorkflowStream } from '../workflow'

function makeStreamResponse(chunks: string[]) {
  const encoder = new TextEncoder()
  let i = 0
  return {
    ok: true,
    status: 200,
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

describe('createWorkflowSSEFrameParser（issue #289 纯函数解析）', () => {
  it('解析单帧命名事件', () => {
    const parser = createWorkflowSSEFrameParser()
    const frames = parser.push('event: node_start\ndata: {"node_id":"n1","status":"running"}\n\n')
    expect(frames).toEqual([{ event: 'node_start', data: { node_id: 'n1', status: 'running' } }])
  })

  it('跨 chunk 边界分帧（一行被切开、多帧同 chunk）', () => {
    const parser = createWorkflowSSEFrameParser()
    const seen = [
      ...parser.push('event: node_start\nda'),
      ...parser.push('ta: {"node_id":"n1"}\n\nevent: node_complete\ndata: {"node_id":"n1"}\n\n'),
    ]
    expect(seen.map((f) => f.event)).toEqual(['node_start', 'node_complete'])
    expect(seen[0].data).toEqual({ node_id: 'n1' })
  })

  it('容忍 \\r\\n 行尾（代理层重写未造额外事件）', () => {
    const parser = createWorkflowSSEFrameParser()
    const frames = parser.push('event: heartbeat\r\ndata: {"timestamp":"t"}\r\n\r\n')
    expect(frames).toEqual([{ event: 'heartbeat', data: { timestamp: 't' } }])
  })

  it('坏 JSON 帧跳过，后续帧不受影响', () => {
    const parser = createWorkflowSSEFrameParser()
    const frames = parser.push(
      'event: node_start\ndata: {broken\n\nevent: complete\ndata: {"execution_id":7}\n\n',
    )
    expect(frames).toEqual([{ event: 'complete', data: { execution_id: 7 } }])
  })

  it('无 event: 名的 data 帧默认按 message 派发（与 sseService 的默认口径一致）', () => {
    const parser = createWorkflowSSEFrameParser()
    const frames = parser.push('data: {"content":"x"}\n\n')
    expect(frames).toEqual([{ event: 'message', data: { content: 'x' } }])
  })
})

describe('executeWorkflowStream（issue #289 接线前置件）', () => {
  beforeEach(() => {
    localStorage.setItem('docmind_token', '"tok-123"')
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('POST + Bearer 头 + 命名事件逐帧回调（EventSource 时代一个都收不到）', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeStreamResponse([
      'event: workflow_start\ndata: {"execution_id":7,"workflow_name":"wf"}\n\n',
      'event: node_start\ndata: {"node_id":"n1"}\n\n',
      'event: node_complete\ndata: {"node_id":"n1","status":"success"}\n\n',
      'event: complete\ndata: {"execution_id":7,"status":"completed"}\n\n',
    ]))
    vi.stubGlobal('fetch', fetchMock)

    const events: Array<[string, unknown]> = []
    executeWorkflowStream(7, { foo: 'bar' }, (event, data) => events.push([event, data]))

    await vi.waitFor(() => expect(events).toHaveLength(4))
    expect(events.map(([e]) => e)).toEqual([
      'workflow_start', 'node_start', 'node_complete', 'complete',
    ])
    expect(events[0][1]).toEqual({ execution_id: 7, workflow_name: 'wf' })

    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/workflows/7/execute?stream=true')
    expect(init.method).toBe('POST')
    expect(init.headers.Authorization).toBe('Bearer tok-123')
    expect(JSON.parse(init.body)).toEqual({ foo: 'bar' })
  })

  it('返回的停止函数 abort 底层请求（AbortSignal 可见）', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeStreamResponse([
      'event: heartbeat\ndata: {"timestamp":"t"}\n\n',
    ]))
    vi.stubGlobal('fetch', fetchMock)

    const stop = executeWorkflowStream(7, {}, () => {})
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    const signal = fetchMock.mock.calls[0][1].signal as AbortSignal
    expect(signal.aborted).toBe(false)
    stop()
    expect(signal.aborted).toBe(true)
  })

  it('onClose 在流关闭时回调（编辑器用它收尾 executing 状态，防悬挂）', async () => {
    const fetchMock = vi.fn().mockResolvedValue(makeStreamResponse([
      'event: complete\ndata: {"execution_id":7}\n\n',
    ]))
    vi.stubGlobal('fetch', fetchMock)

    const closed = vi.fn()
    executeWorkflowStream(7, {}, () => {}, closed)

    await vi.waitFor(() => expect(closed).toHaveBeenCalledTimes(1))
  })

  it('HTTP 非 2xx → 以 error 事件上报（修复前静默 close，接线后无法感知失败）', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 403 }))

    const events: Array<[string, unknown]> = []
    executeWorkflowStream(7, {}, (event, data) => events.push([event, data]))
    await vi.waitFor(() => expect(events).toHaveLength(1))
    expect(events[0][0]).toBe('error')
    expect(events[0][1]).toEqual({ error: 'HTTP 403' })
  })
})
