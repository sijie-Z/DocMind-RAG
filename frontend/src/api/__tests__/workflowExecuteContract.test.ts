/**
 * B8/B9 契约回归：executeWorkflow（非流式调试执行）的请求形态。
 *
 * 真值（backend/app/api/v1/endpoints/workflow.py:197-220）：
 *   - `stream: bool = True` 是 **query** 参数；
 *   - 请求体直接就是 input_data（`input_data = body if body else {}`；引擎读 input["text"]）。
 * 旧实现把 `{workflow_id, input_data, stream:false}` 全塞进 body → 后端不认 stream
 * → 走 SSE 分支返回事件流 → 前端 `res.data?.data` 恒 undefined（面板静默假「执行完成」，
 * B8）；且 input["text"] 恒空、测试输入到不了工作流（B9）。
 *
 * 变异点：还原旧 body 形态 → 本文件红（见 _uiw_exec_mutation.txt 同批证据）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

const { postMock } = vi.hoisted(() => ({
  postMock: vi.fn().mockResolvedValue({ data: {} }),
}))

vi.mock('@/utils/request', () => ({
  default: {
    get: vi.fn(),
    post: postMock,
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import { executeWorkflow } from '../workflow'

describe('executeWorkflow 请求契约（B8/B9）', () => {
  beforeEach(() => {
    postMock.mockClear()
  })

  it('stream 走 query（false）；body 即 input_data 本体，不再嵌套 workflow_id/input_data', async () => {
    await executeWorkflow(7, { text: '你好' })

    expect(postMock).toHaveBeenCalledTimes(1)
    const [url, body, config] = postMock.mock.calls[0]
    expect(url).toBe('/workflows/7/execute')
    expect(body).toEqual({ text: '你好' })
    expect(config?.params).toEqual({ stream: false })
  })

  it('无输入时 body 为 {}（端点 input_data = body if body else {}）', async () => {
    await executeWorkflow(7)
    expect(postMock.mock.calls[0][1]).toEqual({})
  })
})
