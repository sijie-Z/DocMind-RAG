/**
 * Agent 配置面板只消费 store 里的工具清单，不再自打 /agent/tools。
 *
 * 修前：Agent 页 loadInit 已请求 listTools 写入 store，面板 onMounted 又请求
 * 一次（打开配置面板时同一接口两次）。修复后面板从 store 读取。
 *
 * 变异点：恢复面板内 listTools 请求 → 「未发请求」断言红；
 * 不读 store → 工具名断言红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

import request from '@/utils/request'
import { useAgentStore } from '@/stores/agent'
import AgentConfigPanel from '../AgentConfigPanel.vue'
import zh from '@/locales/zh'
import type { AgentConfig, ToolInfo } from '@/types/agent'

vi.mock('@/utils/request', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}))

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const CONFIG = {
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
} as AgentConfig

const TOOL: ToolInfo = {
  name: 'search_docs',
  description: '检索文档',
  tags: [],
  parameters: {},
  requires_auth: false,
  disabled_by_default: false,
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('AgentConfigPanel · 工具清单来自 store', () => {
  it('渲染 store 工具且不重复请求 /agent/tools', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useAgentStore().tools = [TOOL]

    const wrapper = mount(AgentConfigPanel, {
      props: { config: CONFIG },
      global: { plugins: [pinia, i18n] },
    })
    await flushPromises()
    await wrapper.find('.config-header').trigger('click')

    expect(wrapper.text()).toContain('search_docs')
    expect(request.get, '面板不应再请求工具列表').not.toHaveBeenCalled()
  })
})
