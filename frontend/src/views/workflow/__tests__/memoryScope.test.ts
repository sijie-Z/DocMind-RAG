/**
 * 任务2（P0）· Agent 记忆页「作用域」判别用例。
 *
 * 修前：memory.vue 三处写死 default —— getAgentMemory()（api 默认值）、
 * recallMemory('default', …)、clearMemory('default', …)；而 Agent 运行时把记忆
 * 写在 `session:<会话id>`（backend/app/agent/service.py:414,1131；
 * backend/app/api/v1/endpoints/memory.py 的 _normalize_agent_id 显式允许
 * `session:` 前缀，注释写明「操作员需要用它来清理单个会话」）。
 * 后果：记忆页看不到、也清不掉 Agent 真正产生的会话记忆。
 *
 * 变异点（把实现改回旧形态，对应用例必须红）：
 *  - 任一调用点退回 'default' 常量 → 读取 / 检索 / 清空三条用例分别红；
 *  - 选择器不读会话列表（options 退回只有全局一项）→ 会话选项用例红；
 *  - scopeOptions 退回 setup 期常量 → 语言切换用例红；
 *  - 去掉 ?scope= 读取或回写 → 深链 / URL 同步用例红；
 *  - 非法 scope 不做校验直接发请求 → 非法深链用例红；
 *  - 切换作用域不清空旧检索结果 → 陈旧结果用例红；
 *  - 清空确认文案不写作用域（谎称清空全部记忆）→ 确认文案用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createRouter, createMemoryHistory, type Router } from 'vue-router'
import { createI18n } from 'vue-i18n'
import naive, { NSelect } from 'naive-ui'

interface DialogOptions {
  title?: string
  content?: string
  positiveText?: string
  negativeText?: string
  onPositiveClick?: () => unknown
  onNegativeClick?: () => unknown
}

const { dialogState, dialogApi, msgState } = vi.hoisted(() => {
  const state = {
    decision: 'confirm' as 'cancel' | 'confirm',
    calls: [] as DialogOptions[],
  }
  const record = (opts: DialogOptions) => {
    state.calls.push(opts)
    if (state.decision === 'confirm') return opts.onPositiveClick?.()
    return opts.onNegativeClick?.()
  }
  return {
    dialogState: state,
    dialogApi: { warning: vi.fn(record), error: vi.fn(record) },
    msgState: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn() },
  }
})

// 只替换 useDialog / useMessage（确认框与 toast 交互），其余 naive-ui 组件走真实实现
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return { ...actual, useDialog: () => dialogApi, useMessage: () => msgState }
})

const apiMocks = vi.hoisted(() => ({
  getAgentMemory: vi.fn(),
  recallMemory: vi.fn(),
  clearMemory: vi.fn(),
  listSessions: vi.fn(),
}))

vi.mock('@/api/memory', () => ({
  getAgentMemory: apiMocks.getAgentMemory,
  recallMemory: apiMocks.recallMemory,
  clearMemory: apiMocks.clearMemory,
}))
vi.mock('@/api/agent', () => ({ agentApi: { listSessions: apiMocks.listSessions } }))

import MemoryPage from '../memory.vue'
import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  fallbackLocale: 'en',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh, en, ja, fr },
})

// 真载荷形状：GET /agent/sessions → {success, data:{sessions,total,page,page_size}}
const SESSIONS = [
  { id: 's-1', title: '合同核验会话', message_count: 4, created_at: '2026-11-01T10:00:00', updated_at: '2026-11-02T10:00:00' },
  { id: 's-2', title: '', message_count: 1, created_at: '2026-11-01T09:00:00', updated_at: '2026-11-01T09:30:00' },
]

const MEMORY_EXPORT = {
  agent_id: 'default',
  short_term: [],
  long_term: {},
  working: {},
  reflective: { insights: [], patterns: [], lessons: [] },
  interaction_count: 0,
}

const Dummy = defineComponent({ template: '<div />' })

async function mountPage(search = ''): Promise<{ wrapper: ReturnType<typeof mount>; router: Router }> {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workflow/memory', name: 'AgentMemory', component: Dummy, meta: { title: 'router.agentMemory' } },
      { path: '/workflow', name: 'Workflow', component: Dummy },
      { path: '/dashboard', name: 'Dashboard', component: Dummy },
    ],
  })
  await router.push(`/workflow/memory${search}`)
  await router.isReady()
  const wrapper = mount(defineComponent({ render: () => h(MemoryPage) }), {
    global: { plugins: [naive, router, i18n] },
  })
  await flushPromises()
  await flushPromises()
  return { wrapper, router }
}

type MemoryVm = {
  scope: string
  searchQuery: string
  searchResults: unknown[]
  searchMemory: () => Promise<void>
  clearMemoryType: (type: string) => void
  confirmClearAll: () => void
  loadSessions: () => Promise<void>
}

function vmOf(wrapper: ReturnType<typeof mount>): MemoryVm {
  return wrapper.findComponent(MemoryPage).vm as unknown as MemoryVm
}

function scopeSelect(wrapper: ReturnType<typeof mount>) {
  const select = wrapper.findComponent(NSelect)
  expect(select.exists(), '记忆页缺少作用域选择器（n-select）').toBe(true)
  return select
}

function optionValues(wrapper: ReturnType<typeof mount>): string[] {
  return (scopeSelect(wrapper).props('options') as Array<{ value: string }>).map((o) => o.value)
}

function optionLabels(wrapper: ReturnType<typeof mount>): string[] {
  return (scopeSelect(wrapper).props('options') as Array<{ label: string }>).map((o) => o.label)
}

async function chooseScope(wrapper: ReturnType<typeof mount>, value: string) {
  scopeSelect(wrapper).vm.$emit('update:value', value)
  await flushPromises()
  await nextTick()
}

beforeEach(() => {
  vi.clearAllMocks()
  dialogState.decision = 'confirm'
  dialogState.calls.length = 0
  i18n.global.locale.value = 'zh'
  apiMocks.getAgentMemory.mockResolvedValue({ data: { data: MEMORY_EXPORT } })
  apiMocks.recallMemory.mockResolvedValue({
    data: { data: { query: 'q', results: [{ id: 'm1', content: '命中内容', memory_type: 'short_term', importance: 0.5 }], count: 1 } },
  })
  apiMocks.clearMemory.mockResolvedValue({ data: { success: true } })
  apiMocks.listSessions.mockResolvedValue({ data: { data: { sessions: SESSIONS, total: 2, page: 1, page_size: 20 } } })
})

afterEach(() => {
  i18n.global.locale.value = 'zh'
})

describe('记忆页作用域 · 读取', () => {
  it('无 ?scope= 时默认仍读全局 default（防回退：不得改掉既有默认行为）', async () => {
    const { wrapper } = await mountPage()
    expect(apiMocks.getAgentMemory).toHaveBeenCalledWith('default')
    expect(scopeSelect(wrapper).props('value')).toBe('default')
    wrapper.unmount()
  })

  it('?scope=session:s-1 深链直达该会话记忆（修前恒读 default → 红）', async () => {
    const { wrapper } = await mountPage('?scope=session:s-1')
    expect(apiMocks.getAgentMemory).toHaveBeenCalledWith('session:s-1')
    expect(scopeSelect(wrapper).props('value')).toBe('session:s-1')
    wrapper.unmount()
  })

  it('非法 ?scope= 回落 default，不把非法命名空间发给后端（后端 1-64 位 [a-zA-Z0-9_:-]）', async () => {
    const { wrapper } = await mountPage('?scope=..%2Fevil%20id')
    expect(apiMocks.getAgentMemory).toHaveBeenCalledWith('default')
    expect(apiMocks.getAgentMemory).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('选项来自最近 Agent 会话：全局 + session:<id>；无标题的会话如实显示 id', async () => {
    const { wrapper } = await mountPage()
    expect(apiMocks.listSessions).toHaveBeenCalled()
    expect(optionValues(wrapper)).toEqual(expect.arrayContaining(['default', 'session:s-1', 'session:s-2']))
    expect(optionLabels(wrapper)).toEqual(expect.arrayContaining(['合同核验会话', 's-2']))
    wrapper.unmount()
  })

  it('深链会话不在最近列表时仍可选项（不静默改回 default，如实标注）', async () => {
    const { wrapper } = await mountPage('?scope=session:gone')
    expect(optionValues(wrapper)).toContain('session:gone')
    expect(apiMocks.getAgentMemory).toHaveBeenCalledWith('session:gone')
    wrapper.unmount()
  })
})

describe('记忆页作用域 · 检索与清空', () => {
  it('切到会话作用域后：读取 / 检索 / 按类型清空 / 清空全部都用该命名空间', async () => {
    const { wrapper } = await mountPage()
    await chooseScope(wrapper, 'session:s-1')
    expect(apiMocks.getAgentMemory).toHaveBeenLastCalledWith('session:s-1')

    const vm = vmOf(wrapper)
    vm.searchQuery = '付款期限'
    await vm.searchMemory()
    expect(apiMocks.recallMemory).toHaveBeenCalledWith('session:s-1', '付款期限')

    vm.clearMemoryType('short_term')
    await flushPromises()
    expect(apiMocks.clearMemory).toHaveBeenCalledWith('session:s-1', 'short_term')

    vm.confirmClearAll()
    await flushPromises()
    expect(apiMocks.clearMemory).toHaveBeenLastCalledWith('session:s-1')
    wrapper.unmount()
  })

  it('切回全局后检索同样回到 default（作用域不是单向开关）', async () => {
    const { wrapper } = await mountPage('?scope=session:s-1')
    await chooseScope(wrapper, 'default')
    const vm = vmOf(wrapper)
    vm.searchQuery = 'x'
    await vm.searchMemory()
    expect(apiMocks.recallMemory).toHaveBeenLastCalledWith('default', 'x')
    wrapper.unmount()
  })

  it('切换作用域清空上一作用域的检索结果（陈旧命中不得冒充当前作用域）', async () => {
    const { wrapper } = await mountPage()
    const vm = vmOf(wrapper)
    vm.searchQuery = 'q'
    await vm.searchMemory()
    await flushPromises()
    expect(vm.searchResults.length).toBe(1)

    await chooseScope(wrapper, 'session:s-1')
    expect(vm.searchResults.length, '切换作用域后仍展示旧作用域命中').toBe(0)
    wrapper.unmount()
  })

  it('清空确认文案写明作用域，不谎称清空 Agent 全部记忆', async () => {
    const { wrapper } = await mountPage('?scope=session:s-1')
    const vm = vmOf(wrapper)

    vm.clearMemoryType('short_term')
    await flushPromises()
    const typeContent = String(dialogState.calls[0]?.content ?? '')
    expect(typeContent).toContain('合同核验会话')
    expect(typeContent).toContain('短期记忆')

    vm.confirmClearAll()
    await flushPromises()
    const allContent = String(dialogState.calls[1]?.content ?? '')
    expect(allContent).toContain('合同核验会话')
    expect(allContent, '不得再声称清空「所有记忆」').not.toContain('所有记忆数据')
    wrapper.unmount()
  })

  it('取消确认则不发清空请求（二次确认仍然有效）', async () => {
    const { wrapper } = await mountPage('?scope=session:s-1')
    dialogState.decision = 'cancel'
    vmOf(wrapper).confirmClearAll()
    await flushPromises()
    expect(apiMocks.clearMemory).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})

describe('记忆页作用域 · URL 同步与失败态', () => {
  it('选择作用域回写 ?scope=；回到全局时清掉该参数（其余 query 保留）', async () => {
    const { wrapper, router } = await mountPage('?from=agent')
    await chooseScope(wrapper, 'session:s-2')
    expect(router.currentRoute.value.query.scope).toBe('session:s-2')
    expect(router.currentRoute.value.query.from).toBe('agent')

    await chooseScope(wrapper, 'default')
    expect(router.currentRoute.value.query.scope).toBeUndefined()
    expect(router.currentRoute.value.query.from).toBe('agent')
    wrapper.unmount()
  })

  it('会话列表失败：如实报错 + 可重试，当前作用域仍可用（不静默假装成功）', async () => {
    apiMocks.listSessions.mockRejectedValueOnce(new Error('boom'))
    const { wrapper } = await mountPage('?scope=session:s-1')
    expect(wrapper.text()).toContain('会话列表加载失败')
    expect(optionValues(wrapper)).toContain('session:s-1')
    expect(apiMocks.getAgentMemory).toHaveBeenCalledWith('session:s-1')

    const retry = wrapper.findAll('button').find((b) => b.text().includes('重试'))
    expect(retry, '缺少会话列表重试出口').toBeTruthy()
    await retry!.trigger('click')
    await flushPromises()
    expect(apiMocks.listSessions).toHaveBeenCalledTimes(2)
    expect(optionValues(wrapper)).toContain('session:s-2')
    wrapper.unmount()
  })
})

describe('记忆页作用域 · 文案四语与实时切换', () => {
  it('作用域标签与选项随 locale 切换（computed，不是 setup 期常量）', async () => {
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('作用域')
    expect(optionLabels(wrapper)).toContain('全局（default）')

    i18n.global.locale.value = 'en'
    await nextTick()
    await flushPromises()
    expect(wrapper.text()).toContain('Scope')
    expect(wrapper.text()).not.toContain('作用域')
    expect(optionLabels(wrapper)).toContain('Global (default)')

    i18n.global.locale.value = 'ja'
    await nextTick()
    expect(optionLabels(wrapper)).toContain('グローバル（default）')

    i18n.global.locale.value = 'fr'
    await nextTick()
    expect(optionLabels(wrapper)).toContain('Globale (default)')
    wrapper.unmount()
  })

  it('清空当前作用域的按钮文案四语齐全（不再写死「清空所有」）', async () => {
    const { wrapper } = await mountPage()
    expect(wrapper.text()).toContain('清空当前作用域')
    expect(wrapper.text()).not.toContain('清空所有')

    i18n.global.locale.value = 'en'
    await nextTick()
    expect(wrapper.text()).toContain('Clear current scope')
    wrapper.unmount()
  })
})