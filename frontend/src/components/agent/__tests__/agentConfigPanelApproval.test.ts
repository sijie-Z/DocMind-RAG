/**
 * T16 · HITL 审批模式前端开关：在 Agent 配置面板选择 → 保存（PUT）→ GET 回读一致。
 *
 * 后端契约（backend/app/agent/approval.py:46、config.py:204）：
 *   approval_mode ∈ {never, risky, always}，默认 risky；
 *   GET/PUT /agent/config 的 to_dict 信封携带该字段（agent.py:1346-1414）。
 *
 * 传输层替身：只 mock @/utils/request，内存版 GET/PUT /agent/config 共享同一份
 * 存储——保存后回读走真实 agentApi 代码，杜绝"测试替身自证"。
 *
 * 变异点：
 * - 面板无审批选择 / 选项与后端枚举不一致 → 用例 1 红；
 * - apply 载荷漏 approval_mode（或保存后回读丢失）→ 用例 2 红；
 * - 前端默认配置不再是 risky → 用例 3 红；
 * - chat 请求体不带 approval_mode → 用例 4 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import type { Mock } from 'vitest'

vi.mock('@/utils/request', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}))

import request from '@/utils/request'
import { agentApi } from '@/api/agent'
import { useAgentStore } from '@/stores/agent'
import type { AgentConfig } from '@/types/agent'
import AgentConfigPanel from '../AgentConfigPanel.vue'

const mocked = request as unknown as {
  get: Mock
  post: Mock
  put: Mock
  delete: Mock
}

/** 内存假服务端：GET/PUT /agent/config 共享一份存储（默认 risky，同后端）。 */
function installFakeConfigServer(initial: Partial<AgentConfig> = {}) {
  let stored: Record<string, unknown> = {
    model: 'deepseek-v4-flash',
    temperature: 0.1,
    max_tokens: 4096,
    enable_planning: true,
    enable_reflection: true,
    enable_tools: true,
    enable_memory: true,
    enable_thinking: true,
    personality: 'balanced',
    disabled_tools: [],
    max_plan_steps: 10,
    max_retries_per_step: 3,
    approval_mode: 'risky',
    ...initial,
  }
  mocked.get.mockImplementation((url: string) => {
    if (url === '/agent/config') {
      return Promise.resolve({ data: { success: true, data: { ...stored } } })
    }
    // AgentConfigPanel onMounted 拉工具列表（listTools）
    return Promise.resolve({ data: { success: true, data: [] } })
  })
  mocked.put.mockImplementation((url: string, body: Record<string, unknown>) => {
    if (url === '/agent/config') {
      stored = { ...stored, ...body }
      return Promise.resolve({ data: { success: true, data: { ...stored } } })
    }
    return Promise.resolve({ data: { success: true } })
  })
  return { read: () => ({ ...stored }) as unknown as AgentConfig }
}

async function mountPanelFromServer() {
  const res = await agentApi.getConfig()
  const config = res.data.data as AgentConfig
  const w = mount(AgentConfigPanel, { props: { config } })
  // 面板默认折叠：展开后才有配置项
  await w.find('.config-header').trigger('click')
  return { w, config }
}

beforeEach(() => {
  vi.clearAllMocks()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('AgentConfigPanel · HITL 审批模式', () => {
  it('提供三档审批选择（与后端枚举一致），默认显示 risky', async () => {
    installFakeConfigServer()
    const { w } = await mountPanelFromServer()

    const select = w.find('.approval-mode-select')
    expect(select.exists()).toBe(true)
    expect(select.findAll('option').map((o) => (o.element as HTMLOptionElement).value)).toEqual([
      'never',
      'risky',
      'always',
    ])
    expect((select.element as HTMLSelectElement).value).toBe('risky')
  })

  it('选择 always 并保存后，GET 回读一致（含重挂载后的选择器状态）', async () => {
    installFakeConfigServer()
    const { w } = await mountPanelFromServer()

    await w.find('.approval-mode-select').setValue('always')
    await w.find('.apply-btn').trigger('click')

    const emitted = w.emitted('apply')
    expect(emitted).toBeTruthy()
    const payload = emitted![0][0] as AgentConfig
    expect(payload.approval_mode).toBe('always')

    // 与视图层 handleConfigApply 相同的保存路径：PUT /agent/config
    await agentApi.updateConfig(payload)
    expect(mocked.put).toHaveBeenCalledWith(
      '/agent/config',
      expect.objectContaining({ approval_mode: 'always' }),
    )

    // GET 回读：服务端存储与提交一致
    const readback = await agentApi.getConfig()
    const saved = readback.data.data as AgentConfig
    expect(saved.approval_mode).toBe('always')

    // 回读载荷重挂载：选择器如实显示保存值
    const w2 = mount(AgentConfigPanel, { props: { config: saved } })
    await w2.find('.config-header').trigger('click')
    expect((w2.find('.approval-mode-select').element as HTMLSelectElement).value).toBe('always')
  })

  it('默认值保持 risky：前端默认配置与后端一致，未改动保存原样回写', async () => {
    setActivePinia(createPinia())
    expect(useAgentStore().config.approval_mode).toBe('risky')

    installFakeConfigServer()
    const { w } = await mountPanelFromServer()
    await w.find('.apply-btn').trigger('click')
    const payload = w.emitted('apply')![0][0] as AgentConfig
    expect(payload.approval_mode).toBe('risky')
  })
})

describe('agentApi.chat · 审批模式随请求上送', () => {
  it('approvalMode=always → 请求体 approval_mode=always（后端 AgentChatRequest 同名字段）', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => ({ done: true, value: undefined }),
        }),
      },
    } as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)

    await agentApi.chat('你好', () => {}, { approvalMode: 'always' })

    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(init.body)).toEqual(
      expect.objectContaining({ approval_mode: 'always' }),
    )
  })

  it('未显式传入 approvalMode → 请求体省略该字段（沿用服务端已保存配置）', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => ({ done: true, value: undefined }),
        }),
      },
    } as unknown as Response)
    vi.stubGlobal('fetch', fetchMock)

    await agentApi.chat('你好', () => {})

    const [, init] = fetchMock.mock.calls[0]
    expect(JSON.parse(init.body)).not.toHaveProperty('approval_mode')
  })
})
