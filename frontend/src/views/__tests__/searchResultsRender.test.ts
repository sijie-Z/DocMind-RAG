/**
 * P1-3 判别用例（UI 复验 round2 第三处）：搜索请求已 200、结果渲染崩溃。
 *
 * 真 UI 复验现场：展示层抛
 * TypeError: Cannot read properties of undefined (reading 'toLowerCase')
 * at getFileTypeIcon (search.vue)，页面进「页面渲染异常」。直因：模板读
 * result.file_type，而真实响应（下方 REAL_RESULT 为 :8000 POST
 * /knowledge/search 抓包原文结构化转录，字段逐字保留、text 段截断）每个
 * result 只有 {id, text, score, metadata, source, has_keyword, has_vector,
 * rewrite_hits, fresh_factor} —— 无 file_type/filename/content/file_size/
 * upload_time/relevance_score 平铺字段。UI 搜索此前从未成功（100% 422），
 * 该渲染路径从未被执行过 → 历史暗雷。
 * （issue #185 起后端新增顶层 relevance_score = 重排绝对分，score 仍是相对
 * 排序分；本文件同步钉住「界面只展示重排分，不再 score×100 当相关度 %」。）
 *
 * 修复：展示层经访问器从真实字段推导（文件类型←文件名扩展名、得分←score、
 * 正文←text、文件名←source/metadata），推导不出的（大小/日期）不渲染；
 * getFileTypeIcon 空/未知 → DocumentOutline 兜底，永不 toLowerCase undefined。
 *
 * 变异点：
 * - 还原修复前图标路径（模板读 result.file_type + toLowerCase 无防御）
 *   → 「真实响应」用例精确红（TypeError）；
 * - 还原详情模态读 selectedResult.file_type → 「详情模态」用例红（TypeError）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'

import SearchView from '../search.vue'
import { searchKnowledge } from '@/api/search'
import type { SearchResult } from '@/api/search'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'

// 视图依赖的 api 全部打桩；importOriginal 保住 buildSearchPayload 等纯函数。
vi.mock('@/api/search', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/search')>()
  return {
    ...actual,
    searchKnowledge: vi.fn(),
    getSearchSuggestions: vi.fn().mockResolvedValue([]),
    getSearchStats: vi.fn().mockResolvedValue({ total_files: 2, file_types: {}, recent_searches: [] }),
  }
})
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  // W2-T7：search.vue 新增 useRoute（?q= 回填）；本文件不涉及 query，给空对象即可
  useRoute: () => ({ query: {} }),
}))

const mockedSearch = vi.mocked(searchKnowledge)

// ── Fixtures ────────────────────────────────────────────────────────────
// 真实响应结果项（抓包原文；text/snippet 为可读性截断，其余字段逐字保留）
const REAL_RESULT: SearchResult = {
  id: '7af44457-b0d1-419c-b5f8-182ef87383a5',
  text: '# DocMind 系统架构设计本文档描述了 DocMind 企业级 RAG 知识库系统的整体架构设计。## 系统分层架构### 1. 表现层 (Frontend)- Vue 3 + TypeScript 构建的单页应用- Naive UI 组件库提供统一界面风格…（截断）',
  // issue #185：score 是相对排序分（只用于排序）；界面展示的是重排绝对分。
  score: 0.618,
  relevance_score: 0.83,
  metadata: {
    filename: 'architecture.md',
    snippet: '# DocMind 系统架构设计本文档描述了 DocMind 企业级 RAG 知识库系统的整体架构设计。## 系统分层架构…（截断）',
    overlap_score: 0.5,
    mmr_score: 0,
  },
  source: { filename: 'architecture.md', document_id: '7af44457-b0d1-419c-b5f8-182ef87383a5' },
  has_keyword: false,
  has_vector: false,
  rewrite_hits: 1,
  fresh_factor: 1,
}

// 最小形状（schema 必备字段；metadata/source 全空）
const MINIMAL_RESULT: SearchResult = {
  id: 'min-1',
  text: '最小形状正文',
  score: 0.5,
  metadata: {},
  source: {},
}

// ── Mount（先例照 issue #295 errorRendering.test.ts）────────────────────
const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

// 视图在 setup 里调 useDedupedMessage()（naive-ui useMessage 需要 Provider）
const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(SearchView) }),
})

// naive-ui 组件内部 name 即 "Modal"/"Icon"（非 N 前缀），stub 键按库内实名；
// Icon/Modal 用渲染默认插槽的 stub（文件图标 svg / 模态内容需真正出屏）。
const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })
const ModalStub = defineComponent({
  name: 'Modal',
  setup(_, { slots }) { return () => h('div', { class: 'n-modal-stub' }, slots.default?.()) },
})

const stubs = {
  Icon: SlotStub('Icon'),
  Modal: ModalStub,
  Tag: SlotStub('Tag'),
  Button: true,
  Spin: true,
  RadioGroup: true,
  Radio: true,
  CheckboxGroup: true,
  Checkbox: true,
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
  const wrapper = mount(Host, {
    global: { plugins: [pinia, i18n], stubs },
  })
  return wrapper
}

async function doSearch(wrapper: ReturnType<typeof mountView>, results: SearchResult[]) {
  mockedSearch.mockResolvedValue(results)
  await wrapper.find('input').setValue('架构')
  await wrapper.find('button').trigger('click')
  await flushPromises()
  await nextTick()
}

function findCard(wrapper: ReturnType<typeof mountView>, text: string) {
  const card = wrapper.findAll('.group').find((w) => w.text().includes(text))
  expect(card, `结果卡（含 ${text}）应在屏`).toBeTruthy()
  return card!
}

beforeEach(() => {
  mockedSearch.mockReset()
})

describe('SearchView · 结果渲染（P1-3 真实响应不崩）', () => {
  it('真实响应 fixture：列表渲染不崩、文件名/重排分/正文推导正确、无 NaN/Invalid Date', async () => {
    const wrapper = mountView()
    await doSearch(wrapper, [REAL_RESULT])

    const text = wrapper.text()
    expect(text).toContain('architecture.md')   // 文件名 ← source/metadata
    // issue #185：只展示重排绝对分（relevance_score），不再把排序分 0.618 ×100
    expect(text).toContain('重排分: 0.83')
    expect(text).not.toContain('61.8%')
    expect(text).toContain('DocMind 系统架构设计') // 正文 ← text
    expect(text).not.toContain('NaN')
    expect(text).not.toContain('Invalid Date')
    expect(text).not.toContain('undefined')

    // 文件图标真实渲染（类型由文件名扩展名推导 → NewspaperOutline → svg）
    const card = findCard(wrapper, 'architecture.md')
    expect(card.find('.w-10.h-10').find('svg').exists()).toBe(true)
  })

  it('最小形状 fixture（仅必备字段，无重排分）：同样不崩、无 NaN/Invalid Date/undefined、不展示分数', async () => {
    const wrapper = mountView()
    await doSearch(wrapper, [MINIMAL_RESULT])

    const text = wrapper.text()
    expect(text).toContain('最小形状正文')
    // issue #185：无 relevance_score → 不展示任何分数（列表顺序即序位）
    expect(text).not.toContain('重排分')
    expect(text).not.toContain('NaN')
    expect(text).not.toContain('Invalid Date')
    expect(text).not.toContain('undefined')
    // 类型不可推导（无文件名）→ 图标仍是 DocumentOutline 兜底、不崩
    expect(findCard(wrapper, '最小形状正文').find('svg').exists()).toBe(true)
  })

  it('详情模态（点开结果卡）：类型列推导为 MD、重排分 0.83、无 NaN/Invalid Date', async () => {
    const wrapper = mountView()
    await doSearch(wrapper, [REAL_RESULT])

    const card = findCard(wrapper, 'architecture.md')
    await card.trigger('click')
    await nextTick()

    const modal = wrapper.find('.n-modal-stub')
    expect(modal.exists()).toBe(true)
    expect(modal.text()).toContain('MD')       // fileTypeOf → 'md'.toUpperCase()
    // issue #185：模态同样只展示重排绝对分，不展示排序分百分比
    expect(modal.text()).toContain('重排分')
    expect(modal.text()).toContain('0.83')
    expect(modal.text()).not.toContain('61.8%')
    expect(modal.text()).not.toContain('NaN')
    expect(modal.text()).not.toContain('Invalid Date')
    expect(modal.text()).not.toContain('undefined')
  })

  it('relevance 排序保持后端（重排）顺序，不用相对 score 重排（issue #185）', async () => {
    // 后端顺序 first→second（重排绝对分 8.5 > 1.0）；若前端按相对 score 重排，
    // second（0.9）会越过 first（0.2）——那正是把 rerank 结果打回原序的缺陷。
    const first: SearchResult = {
      ...REAL_RESULT,
      id: 'first-1',
      score: 0.2,
      relevance_score: 8.5,
      source: { filename: 'first.md' },
      metadata: { filename: 'first.md', snippet: '第一条' },
    }
    const second: SearchResult = {
      ...REAL_RESULT,
      id: 'second-2',
      score: 0.9,
      relevance_score: 1.0,
      source: { filename: 'second.md' },
      metadata: { filename: 'second.md', snippet: '第二条' },
    }
    const wrapper = mountView()
    await doSearch(wrapper, [first, second])

    const cards = wrapper.findAll('.group').filter((w) => w.find('h3').exists())
    expect(cards).toHaveLength(2)
    expect(cards[0].text()).toContain('first.md')
    expect(cards[1].text()).toContain('second.md')
  })

  it('负例：空结果 → 不渲染结果卡、无异常', async () => {
    const wrapper = mountView()
    await doSearch(wrapper, [])

    expect(wrapper.findAll('.group').filter((w) => w.find('h3').exists())).toHaveLength(0)
    expect(wrapper.text()).not.toContain('NaN')
  })
})
