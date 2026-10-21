/**
 * T10 空窗补丁 · 搜索页「文件类型」筛选不再渲染（问题清单第二轮 §三.16）。
 *
 * 背景：勾选文件类型后前端会发送 file_types，但后端 KnowledgeSearchRequest
 * 不消费该字段（api/search.ts 自认「预存在漂移」，pydantic 静默丢弃）——
 * 筛选静默空转：勾了没反应，还误导用户以为筛选生效。完整修复（后端
 * ES filter + 值域映射 docx/xlsx→word/excel）依赖 #225（search_mode 归一）
 * 先合入后 rebase。
 *
 * 空窗处置（守最小）：search.vue 筛选区块停止渲染（v-if 开关），状态与
 * 传参代码原位保留（恢复点=该开关）。selectedFileTypes 因此恒为空数组，
 * buildSearchPayload 对其条件展开 → 请求体不含 file_types（本用例一并锁定）。
 *
 * 变异点：删 search.vue 筛选区块的停止渲染开关 → 「不再渲染」断言精确红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'

// 视图依赖的 api 全部打桩；importOriginal 保住 buildSearchPayload 等纯函数。
vi.mock('@/api/search', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/search')>()
  return {
    ...actual,
    searchKnowledge: vi.fn().mockResolvedValue([]),
    getSearchSuggestions: vi.fn().mockResolvedValue([]),
    getSearchStats: vi.fn().mockResolvedValue({ total_files: 2, file_types: {}, recent_searches: [] }),
  }
})
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ query: {} }),
}))

import SearchView from '../search.vue'
import { searchKnowledge } from '@/api/search'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'

const mockedSearch = vi.mocked(searchKnowledge)

// ── Mount（先例照 searchResultsRender.test.ts）────────────────────────────
const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(SearchView) }),
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })
const ModalStub = defineComponent({
  name: 'Modal',
  setup(_, { slots }) { return () => h('div', { class: 'n-modal-stub' }, slots.default?.()) },
})

// 刻意不 stub Checkbox/CheckboxGroup：筛选若仍在渲染，真实 n-checkbox 会出屏（可数可断言）
const stubs = {
  Icon: SlotStub('Icon'),
  Modal: ModalStub,
  Tag: SlotStub('Tag'),
  Button: true,
  Spin: true,
  RadioGroup: true,
  Radio: true,
  Select: true,
  Space: true,
}

function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.userInfo = {
    id: 1, username: 'tester', email: 't@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
  } as UserInfo
  return mount(Host, { global: { plugins: [pinia, i18n], stubs } })
}

beforeEach(() => {
  mockedSearch.mockReset()
  mockedSearch.mockResolvedValue([])
})

describe('T10 · 搜索页文件类型筛选空窗补丁', () => {
  it('筛选控件不再渲染（标题词条与 checkbox 均无）；其余筛选与搜索主流程不受影响', async () => {
    const wrapper = mountView()

    // 1) 文件类型筛选区块：标题词条不出现、checkbox 一个都不渲染
    expect(wrapper.text()).not.toContain(zh.search.fileType)
    expect(wrapper.findAll('.n-checkbox')).toHaveLength(0)

    // 2) 其余筛选区块（搜索模式）仍在 —— 防误伤整张筛选卡
    expect(wrapper.text()).toContain(zh.search.type)

    // 3) 搜索主流程照常：请求发出、字段完整，且请求体不含 file_types
    //    （selectedFileTypes 恒空 → buildSearchPayload 条件展开剔除）
    await wrapper.find('input').setValue('架构')
    await wrapper.find('button').trigger('click')
    await flushPromises()
    await nextTick()
    expect(mockedSearch).toHaveBeenCalledTimes(1)
    const payload = mockedSearch.mock.calls[0][0]
    expect(payload.query).toBe('架构')
    expect('file_types' in payload).toBe(false)
  })
})
