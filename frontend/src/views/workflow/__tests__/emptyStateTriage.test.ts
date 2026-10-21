/**
 * E14/C1+D1+D2 · 工作流列表页空态分流与错误治理判别用例。
 *
 * C1 空态三分：
 *  - 空库（无关键词）→ 「暂无工作流」+ 创建第一个工作流；
 *  - 搜索无命中（关键词过滤后为空）→ 「没有匹配的工作流」+ 清除搜索；
 *  - 错误 → 错误态 + 重试。
 * D1：删除/复制失败只弹一次 toast，后端 detail 原文（axios 英文不）上屏。
 * D2：列表加载失败清空旧结果，重试成功恢复。
 *
 * 变异点：空态回退单一文案 / catch 不清 workflows / catch 回退 error.message / 重试失效 → 对应用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NDialogProvider } from 'naive-ui'

import WorkflowList from '../index.vue'
import { getWorkflows, getWorkflow, createWorkflow, deleteWorkflow } from '@/api/workflow'
import zh from '@/locales/zh'

vi.mock('@/api/workflow', () => ({
  getWorkflows: vi.fn(),
  getWorkflow: vi.fn(),
  createWorkflow: vi.fn(),
  deleteWorkflow: vi.fn(),
}))

const messageSpies = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn(),
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

const LIST_ITEM = {
  id: 3,
  name: '流程A',
  description: '描述',
  is_active: true,
  created_at: '2026-10-01T00:00:00',
  updated_at: '2026-10-02T00:00:00',
}

const listResponse = (items: unknown[]) => ({ data: { data: { items, total: items.length } } })

function axiosError(status: number, data: unknown, message: string) {
  return Object.assign(new Error(message), {
    isAxiosError: true,
    config: { url: '/x' },
    response: status === 0 ? undefined : { status, data },
  })
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NDialogProvider, null, { default: () => h(WorkflowList) }),
})

async function mountList() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'Home', component: { template: '<div />' } }],
  })
  await router.push('/')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(Host, { global: { plugins: [naive, pinia, router, i18n] } })
  await flushPromises()
  await nextTick()
  return wrapper
}

function findButtonByText(wrapper: Awaited<ReturnType<typeof mountList>>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

const vmOf = (wrapper: Awaited<ReturnType<typeof mountList>>) =>
  wrapper.findComponent(WorkflowList).vm as unknown as {
    loadWorkflows: () => Promise<void>
    deleteWorkflow: (id: number) => void
    duplicateWorkflow: (item: typeof LIST_ITEM) => Promise<void>
    searchKeyword: string
    workflows: unknown[]
  }

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getWorkflows).mockResolvedValue(listResponse([LIST_ITEM]) as never)
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('workflow 列表页 · C1 空态分流', () => {
  it('空库（无关键词）→ 「暂无工作流」+ 创建第一个工作流', async () => {
    vi.mocked(getWorkflows).mockResolvedValue(listResponse([]) as never)
    const wrapper = await mountList()

    expect(wrapper.text()).toContain(zh.workflow.empty)
    expect(findButtonByText(wrapper, zh.workflow.createFirst), '空库应引导创建').toBeTruthy()
    expect(wrapper.text()).not.toContain(zh.workflow.searchEmpty)
  })

  it('搜索无命中 → 「没有匹配的工作流」+ 清除搜索；不劝创建', async () => {
    const wrapper = await mountList()
    await wrapper.find('input').setValue('不存在的流程')
    await nextTick()

    expect(wrapper.text()).toContain(zh.workflow.searchEmpty)
    expect(findButtonByText(wrapper, zh.workflow.clearSearch), '应提供清除搜索入口').toBeTruthy()
    expect(wrapper.text()).not.toContain(zh.workflow.createFirst)

    // 清除搜索 → 列表恢复
    await findButtonByText(wrapper, zh.workflow.clearSearch)!.trigger('click')
    await nextTick()
    expect(wrapper.text()).toContain('流程A')
  })
})

describe('workflow 列表页 · D2 加载失败清旧结果 + 重试', () => {
  it('加载失败 → 空库旧卡片清除、错误态 + 重试；重试成功恢复', async () => {
    const wrapper = await mountList()
    expect(wrapper.text()).toContain('流程A')

    vi.mocked(getWorkflows).mockRejectedValueOnce(
      axiosError(500, { detail: '内部堆栈 /srv/app/y.py:9' }, 'Request failed with status code 500'),
    )
    await vmOf(wrapper).loadWorkflows()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain(zh.workflow.loadFailed)
    expect(wrapper.text()).not.toContain('流程A')
    expect(vmOf(wrapper).workflows, '失败后工作流列表状态必须清空').toEqual([])
    expect(wrapper.text()).not.toContain('/srv/app/y.py')

    const retry = findButtonByText(wrapper, zh.common.retry)
    expect(retry, '错误态应提供重试按钮').toBeTruthy()
    vi.mocked(getWorkflows).mockResolvedValue(listResponse([LIST_ITEM]) as never)
    await retry!.trigger('click')
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain('流程A')
  })
})

describe('workflow 列表页 · D1 错误文案单一通道', () => {
  it('删除失败 → 一次 toast、展示后端 detail；列表保持原样（状态如实）', async () => {
    const wrapper = await mountList()
    vi.mocked(deleteWorkflow).mockRejectedValueOnce(
      axiosError(400, { detail: '工作流被引用，无法删除' }, 'Request failed with status code 400'),
    )

    vmOf(wrapper).deleteWorkflow(LIST_ITEM.id)
    await flushPromises()
    await nextTick()

    const confirm = Array.from(document.body.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === zh.workflow.confirmDeletePositive,
    )
    expect(confirm, '确认对话框应打开').toBeTruthy()
    confirm!.click()
    await flushPromises()
    await nextTick()

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const text = String(messageSpies.error.mock.calls[0][0])
    expect(text).toContain('工作流被引用，无法删除')
    expect(text).not.toContain('Request failed with status code')
    // 失败不刷新列表：仅挂载时那一次加载
    expect(getWorkflows).toHaveBeenCalledTimes(1)
  })

  it('复制失败 → 一次 toast、后端 detail 原样呈现', async () => {
    const wrapper = await mountList()
    vi.mocked(getWorkflow).mockRejectedValueOnce(
      axiosError(404, { detail: '工作流不存在' }, 'Request failed with status code 404'),
    )

    await vmOf(wrapper).duplicateWorkflow(LIST_ITEM)
    await flushPromises()

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    expect(String(messageSpies.error.mock.calls[0][0])).toContain('工作流不存在')
    expect(createWorkflow).not.toHaveBeenCalled()
  })
})
