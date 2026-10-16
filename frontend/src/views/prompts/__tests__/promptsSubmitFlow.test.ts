/**
 * F 波（w2tf1）· 提示词「新增模板」保存流回归锁 ——
 * 成功 → 抽屉自动关闭（且无遮罩/内容残留）；失败 → 抽屉保留（可改后重试）。
 *
 * 背景：跨会话实机发现称「保存成功后抽屉遮罩不自动关闭、拦截列表点击」。
 * 复验于 e5b5e5b（三环境：Node/系统 Python Playwright + headed/CDP，全仪器 DOM 采证）：
 * 成功后 `.n-drawer` 与 `.n-drawer-mask` 均随关闭移除（仅剩 pointer-events:none 的
 * 容器，属 naive 懒挂载设计）；发现者现场截图（r4-03c）实为「模板名称已存在」保存
 * 失败态 —— 失败不关 = 设计行为，被误读为「保存成功后不关」。本用例把两个方向
 * 钉死为回归锁，并以变异法验证检测力（删 close 行 → 成功用例红）。
 *
 * 变异点：handleSubmit 成功路径不置 showDrawer=false → 「成功关闭」用例红；
 *        失败路径误关抽屉 → 「失败保留」用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'
import { createPinia } from 'pinia'
import {
  NDialogProvider, NDrawer, NDrawerContent, NForm, NFormItem, NInput, NRadioGroup, NRadio,
  NSpace, NButton, NIcon, NSelect, NBadge, NSpin, NSkeleton, NResult, NTag, NEmpty,
} from 'naive-ui'
import zh from '@/locales/zh'

import PromptsView from '../index.vue'

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

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })
const router = createRouter({ history: createMemoryHistory(), routes: [] })

const Host = defineComponent({
  render: () => h(NDialogProvider, null, { default: () => h(PromptsView) }),
})

// vitest 环境不加载 unplugin-vue-components：n-* 需手动全局注册（既有页面测试同款）
const naiveComponents = {
  NDrawer, NDrawerContent, NForm, NFormItem, NInput, NRadioGroup, NRadio, NSpace,
  NButton, NIcon, NSelect, NBadge, NSpin, NSkeleton, NResult, NTag, NEmpty,
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const wrappers: ReturnType<typeof mount>[] = []

async function mountView() {
  const wrapper = mount(Host, {
    attachTo: document.body,
    global: {
      plugins: [i18n, router, createPinia()],
      components: naiveComponents,
      // 关闭 VTU 默认的 Transition 打桩：需走真实过渡链（Vue duration 兜底推进），
      // 否则抽屉关闭后 DOM 不落地，断言失真
      stubs: { transition: false, 'transition-group': false },
    },
  })
  wrappers.push(wrapper)
  await flushPromises()
  await sleep(20)
  await flushPromises()
  return wrapper
}

function clickButtonByText(texts: string | string[]) {
  const list = Array.isArray(texts) ? texts : [texts]
  const btn = Array.from(document.querySelectorAll('button')).find((b) =>
    list.some((text) => (b.textContent || '').includes(text)),
  )
  if (!btn) throw new Error(`button not found: ${list.join(' / ')}`)
  ;(btn as HTMLButtonElement).click()
}

function setNativeValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  el.value = value
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

async function openDrawerAndFill(name: string, content: string) {
  // CW1 链并：页面新建按钮文案以并入树实文为准（「创建模板」；旧稿为「新增模板」）——
  // 仅对齐交互定位文本，全部断言语义零改。
  clickButtonByText(['新建模板', '新增模板', '创建模板'])
  await flushPromises()
  await sleep(30)
  const drawer = document.querySelector('.n-drawer')
  if (!drawer) throw new Error('drawer not rendered')
  setNativeValue(drawer.querySelector('input') as HTMLInputElement, name)
  setNativeValue(drawer.querySelector('textarea') as HTMLTextAreaElement, content)
  await flushPromises()
  await sleep(10)
}

async function submitAndSettle() {
  // CW1 链并：提交按钮文案以并入树页面实文为准（新建态为「创建」；旧稿为「保存」）。
  // 范围限定在抽屉内、按整文精确匹配，避免与页面「创建模板 / 新建模板」入口按钮歧义。
  const drawer = document.querySelector('.n-drawer')
  const btn = Array.from((drawer ?? document).querySelectorAll('button')).find((b) => {
    const text = (b.textContent || '').trim()
    return text === '保存' || text === '创建'
  })
  if (!btn) throw new Error('submit button not found: 保存 / 创建')
  ;(btn as HTMLButtonElement).click()
  await flushPromises()
  // naive 抽屉关闭为嵌套过渡（遮罩 fade + 本体 slide），jsdom 下 transitionend 不触发，
  // 由 Vue 的 duration 兜底推进——留足兜底时长（实测 ~600ms 量级）
  await sleep(800)
  await flushPromises()
}

describe('提示词「新增模板」保存流回归锁', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.getPrompts.mockResolvedValue({ data: [] })
  })

  afterEach(() => {
    while (wrappers.length > 0) wrappers.pop()!.unmount()
    document.body.innerHTML = ''
  })

  it('成功：创建后抽屉与遮罩均关闭（无残留、不拦截页面）', async () => {
    apiMocks.createPrompt.mockResolvedValue({ data: { id: 1, name: 't-ok' } })
    await mountView()
    await openDrawerAndFill('t-ok', '内容')

    expect(document.querySelector('.n-drawer'), '开抽屉后应渲染抽屉').not.toBeNull()
    expect(document.querySelector('.n-drawer-mask'), '开抽屉后应有遮罩').not.toBeNull()

    await submitAndSettle()

    expect(apiMocks.createPrompt).toHaveBeenCalledTimes(1)
    const payload = apiMocks.createPrompt.mock.calls[0][0] as Record<string, unknown>
    expect(payload.name).toBe('t-ok')
    expect(payload.content).toBe('内容')
    expect(payload.change_note, '创建载荷不得携带 change_note（仅编辑用）').toBeUndefined()
    expect(
      // CW1 链并：成功回执文案以并入树页面实文为准（「创建成功」；旧稿为「添加成功」）——
      // 「成功 toast 必须出现」的语义断言零改，仅对齐文案匹配集合。
      messageSpies.success.mock.calls.some((c) => {
        const text = String(c[0])
        return text.includes('添加成功') || text.includes('创建成功')
      }),
      '成功 toast 应出现',
    ).toBe(true)

    expect(document.querySelector('.n-drawer'), '成功后抽屉应关闭（无残留）').toBeNull()
    expect(document.querySelector('.n-drawer-mask'), '成功后遮罩应关闭（不拦截列表点击）').toBeNull()
  })

  it('失败：抽屉保留（用户可改后重试），遮罩随抽屉保留', async () => {
    const err = Object.assign(new Error('模板名称已存在'), {
      response: { data: { detail: '模板名称已存在' } },
    })
    apiMocks.createPrompt.mockRejectedValueOnce(err)
    await mountView()
    await openDrawerAndFill('dup-name', '内容')

    await submitAndSettle()

    expect(apiMocks.createPrompt).toHaveBeenCalledTimes(1)
    expect(
      messageSpies.error.mock.calls.some((c) => String(c[0]).includes('模板名称已存在')),
      '失败原文应作为错误 toast 展示',
    ).toBe(true)

    expect(document.querySelector('.n-drawer'), '失败后抽屉应保留').not.toBeNull()
    expect(document.querySelector('.n-drawer-mask'), '失败后遮罩在（抽屉仍开，符合预期）').not.toBeNull()
  })
})
