/**
 * F2 判别用例（浏览器验收实锤）：刷新重连回审批现场必须走**真实解包链**。
 *
 * 链路：AgentView.onMounted → restorePendingApproval → 真 agentApi.getTask
 *      → 真模块调用 request.get（此处只 mock request 模块，模拟 axios 响应拦截器
 *      的输出形状：完整 AxiosResponse，信封在 res.data = {success, data}）。
 *
 * 旧病（修复前）：视图读 `res.data`（信封本身）→ data.state 恒 undefined →
 * 恢复分支永不命中 → 刷新后审批卡 count=0。
 * 变异点：把 index.vue 两个消费点改回 `res.data` → 本文件红（已实测，见
 * _hitl_mutations.txt M7）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'

import zh from '@/locales/zh'
import AgentView from '../index.vue'
import ApprovalCard from '@/components/agent/ApprovalCard.vue'
import { useAgentStore } from '@/stores/agent'

// 只 mock request 模块（axios 响应拦截器输出的通道）；
// agentApi / 视图 / store 全部走真实代码——解包链不被测试替身掩盖。
const requestMocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))

vi.mock('@/utils/request', () => ({ default: requestMocks }))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(AgentView) }),
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  Markdown: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
}

function _envelope(data: unknown) {
  return { data: { success: true, data } }
}

async function mountView(pinia: ReturnType<typeof createPinia>) {
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n],
      components: { NSpin, NButton, NTooltip, NIcon },
      stubs,
    },
  })
  await flushPromises()
  return wrapper
}

describe('AgentView · F2 刷新重连（真实解包链）', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.clearAllMocks()
    requestMocks.get.mockImplementation((url: string) => {
      if (url === '/agent/tools') return Promise.resolve(_envelope([]))
      if (url === '/agent/sessions') {
        return Promise.resolve(_envelope({ sessions: [], total: 0, page: 1, page_size: 20 }))
      }
      if (url === '/agent/config') return Promise.resolve(_envelope({}))
      if (url.startsWith('/agent/tasks/t-real')) {
        return Promise.resolve(_envelope({
          task_id: 't-real',
          state: 'waiting_approval',
          is_recoverable: true,
          progress: 0.5,
          completed_steps: 1,
          total_steps: 2,
          current_step_id: 's2',
          last_error: null,
          last_error_type: null,
          query: '帮我执行一段脚本',
          approval: {
            tool_name: 'execute_python',
            tool_args: { code: 'print(123*456)' },
            risk_level: 'high',
            reason: '高风险工具 execute_python（风险等级 high）执行前需人工审批',
          },
          approval_expired: false,
        }))
      }
      return Promise.resolve(_envelope(null))
    })
  })

  it('刷新后从信封内层解包并重建审批卡（旧病：读信封本身 → data.state undefined → 恒不恢复）', async () => {
    localStorage.setItem('docmind.agent.lastTaskId', 't-real')
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAgentStore()

    const wrapper = await mountView(pinia)

    expect(requestMocks.get).toHaveBeenCalledWith('/agent/tasks/t-real')
    expect(store.taskState).toBe('waiting_approval')
    expect(store.pendingApproval?.tool_name).toBe('execute_python')
    expect(store.pendingApproval?.tool_args).toEqual({ code: 'print(123*456)' })
    const card = wrapper.findComponent(ApprovalCard)
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('execute_python')
    wrapper.unmount()
  })

  it('终态任务：不重建卡片并清掉陈旧指针（同链解包）', async () => {
    localStorage.setItem('docmind.agent.lastTaskId', 't-real')
    requestMocks.get.mockImplementation((url: string) => {
      if (url.startsWith('/agent/tasks/t-real')) {
        return Promise.resolve(_envelope({ task_id: 't-real', state: 'cancelled' }))
      }
      return Promise.resolve(_envelope([]))
    })
    const pinia = createPinia()
    setActivePinia(pinia)
    const store = useAgentStore()

    const wrapper = await mountView(pinia)

    expect(store.pendingApproval).toBeNull()
    expect(localStorage.getItem('docmind.agent.lastTaskId')).toBeNull()
    wrapper.unmount()
  })
})
