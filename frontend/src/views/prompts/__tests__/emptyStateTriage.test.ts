/**
 * E14/C1+D1+D2 · 提示词页空态分流与错误治理判别用例。
 *
 * C1：空库 → 「暂无提示词模板」+ 创建模板；筛选/搜索无命中 → 「没有匹配的模板」
 *     + 清除筛选（不得劝创建）。
 * D1：删除失败只弹一次 toast 且展示后端 detail（axios 英文原文禁止上屏）；
 *     加载失败仅走页内错误态（不再重复 toast）。
 * D2：加载失败清空旧列表、错误态 + 重试；重试成功恢复。
 *
 * 变异点：空态回退单一文案 / catch 不清 prompts / 加载失败弹双通道 / 重试失效 → 红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NDialogProvider } from 'naive-ui'

import PromptsView from '../index.vue'
import zh from '@/locales/zh'

const apiMocks = vi.hoisted(() => ({
  getPrompts: vi.fn(),
  createPrompt: vi.fn(),
  updatePrompt: vi.fn(),
  deletePrompt: vi.fn(),
  seedDefaultPrompts: vi.fn(),
  getPromptVersions: vi.fn(),
  restorePromptVersion: vi.fn(),
}))
vi.mock('@/api/prompt', () => apiMocks)

const messageSpies = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn(),
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

const TEMPLATE = {
  id: 1,
  name: '周报模板',
  content: '帮我写周报',
  description: '',
  is_active: true,
  is_system: false,
  category: 'general',
  version: 1,
  created_at: '2026-10-01T00:00:00',
}

function axiosError(status: number, data: unknown, message: string) {
  return Object.assign(new Error(message), {
    isAxiosError: true,
    config: { url: '/x' },
    response: status === 0 ? undefined : { status, data },
  })
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NDialogProvider, null, { default: () => h(PromptsView) }),
})

async function mountView() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', name: 'Chat', component: { template: '<div />' } }],
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

function findButtonByText(wrapper: Awaited<ReturnType<typeof mountView>>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

const vmOf = (wrapper: Awaited<ReturnType<typeof mountView>>) =>
  wrapper.findComponent(PromptsView).vm as unknown as {
    loadPrompts: () => Promise<void>
    handleDelete: (id: number) => void
    searchQuery: string
    activeCategory: string | null
    prompts: unknown[]
  }

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.getPrompts.mockResolvedValue({ data: [TEMPLATE] })
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('提示词页 · C1 空态分流', () => {
  it('空库（无筛选）→ 「暂无提示词模板」+ 创建模板', async () => {
    apiMocks.getPrompts.mockResolvedValue({ data: [] })
    const wrapper = await mountView()

    expect(wrapper.text()).toContain(zh.prompts.empty)
    expect(findButtonByText(wrapper, zh.prompts.create), '空库应引导创建').toBeTruthy()
    expect(wrapper.text()).not.toContain(zh.prompts.searchEmpty)
  })

  it('搜索无命中 → 「没有匹配的模板」+ 清除筛选；不劝创建', async () => {
    const wrapper = await mountView()
    await wrapper.find('input').setValue('不存在的模板')
    await nextTick()

    expect(wrapper.text()).toContain(zh.prompts.searchEmpty)
    const clear = findButtonByText(wrapper, zh.prompts.clearFilters)
    expect(clear, '应提供清除筛选入口').toBeTruthy()
    expect(wrapper.text()).not.toContain(zh.prompts.create)

    await clear!.trigger('click')
    await nextTick()
    expect(wrapper.text()).toContain('周报模板')
  })
})

describe('提示词页 · D2 加载失败清旧结果 + 重试', () => {
  it('加载失败 → 清空旧模板、仅页内错误态（不重复 toast）+ 重试恢复', async () => {
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('周报模板')

    apiMocks.getPrompts.mockRejectedValueOnce(
      axiosError(400, { detail: '模板服务暂不可用' }, 'Request failed with status code 400'),
    )
    await vmOf(wrapper).loadPrompts()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain(zh.prompts.toast.loadFailed)
    expect(wrapper.text()).toContain('模板服务暂不可用')
    expect(wrapper.text()).not.toContain('周报模板')
    expect(vmOf(wrapper).prompts, '失败后提示词列表状态必须清空').toEqual([])
    // 错误由页内错误态唯一呈现：不再叠加全局/页面 toast
    expect(messageSpies.error).not.toHaveBeenCalled()

    const retry = findButtonByText(wrapper, zh.common.retry)
    expect(retry, '错误态应提供重试按钮').toBeTruthy()
    apiMocks.getPrompts.mockResolvedValue({ data: [TEMPLATE] })
    await retry!.trigger('click')
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain('周报模板')
  })
})

describe('提示词页 · D1 错误文案单一通道', () => {
  it('删除失败 → 一次 toast、后端 detail 原样呈现；列表保持不变', async () => {
    const wrapper = await mountView()
    apiMocks.deletePrompt.mockRejectedValueOnce(
      axiosError(400, { detail: '系统模板不可删除' }, 'Request failed with status code 400'),
    )

    vmOf(wrapper).handleDelete(TEMPLATE.id)
    await flushPromises()
    await nextTick()

    const confirm = Array.from(document.body.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === zh.common.confirm,
    )
    expect(confirm, '确认对话框应打开').toBeTruthy()
    confirm!.click()
    await flushPromises()
    await nextTick()

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const text = String(messageSpies.error.mock.calls[0][0])
    expect(text).toContain('系统模板不可删除')
    expect(text).not.toContain('Request failed with status code')
    expect(wrapper.text()).toContain('周报模板')
    expect(apiMocks.getPrompts).toHaveBeenCalledTimes(1)
  })
})
