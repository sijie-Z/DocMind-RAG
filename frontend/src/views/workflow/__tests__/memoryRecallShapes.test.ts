/**
 * B14 契约回归：记忆检索（recall）结果三种形状渲染。
 *
 * 真值（backend/app/services/memory_service.py:604-641）：
 *   - short_term / long_term 项：to_dict 全字段 {id,content,memory_type,importance,…}
 *   - reflective insight：{type:'insight', content}（无 id/memory_type/importance）
 *   - reflective lesson：{type:'lesson', lesson, trigger, solution, created_at}
 * 旧渲染按单一形状读：lesson 项内容空白、标签恒「长期」（memory_type undefined）、
 * 每个无 importance 的行都渲染半空「重要性: 」。
 *
 * 变异点：模板还原旧读点（item.content / 二元标签 / item.importance?.toFixed）→ 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'
import { createRouter, createMemoryHistory } from 'vue-router'
import { createI18n } from 'vue-i18n'

vi.mock('@/api/memory', () => ({
  getAgentMemory: vi.fn().mockResolvedValue({ data: { data: null } }),
  recallMemory: vi.fn(),
  clearMemory: vi.fn(),
}))

import MemoryPage from '../memory.vue'
import { recallMemory } from '@/api/memory'

// 真实响应形状（memory.py:85-102 {success, data:{query, results, count}}）；
// 三种形状与 memory_service.recall 逐一对应
const RECALL_RESULTS = [
  {
    id: 'm1', content: '短期记忆内容', memory_type: 'short_term', importance: 0.8,
    metadata: {}, created_at: '2026-10-10T00:00:00', last_accessed: '2026-10-10T00:00:00', access_count: 1,
  },
  { type: 'insight', content: '一条洞察' },
  { type: 'lesson', lesson: '教训内容', trigger: '场景X', solution: '解决方案Y', created_at: '2026-10-10T00:00:00' },
]

const Host = defineComponent({
  setup() {
    return () =>
      h(NMessageProvider, null, {
        default: () => h(NDialogProvider, null, { default: () => h(MemoryPage) }),
      })
  },
})

// T9：记忆页接入面包屑（useRoute/useRouter + useI18n），挂载基座同步补插件
// （照 realtimeWiring 先例）；空词条下 t() 回显键名，无碍本文件内容断言。
const DummyRoute = defineComponent({ template: '<div />' })
const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh: {} },
})

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/workflow/memory', name: 'AgentMemory', component: DummyRoute },
      { path: '/dashboard', name: 'Dashboard', component: DummyRoute },
    ],
  })
}

async function mountMemory() {
  const router = makeRouter()
  await router.push('/workflow/memory')
  await router.isReady()
  const wrapper = mount(Host, { global: { plugins: [naive, router, i18n] } })
  await flushPromises()
  return wrapper
}

describe('workflow/memory · recall 三形状渲染（B14）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(recallMemory).mockResolvedValue({
      data: { data: { query: '测试', results: RECALL_RESULTS, count: 3 } },
    } as never)
  })

  it('三种形状各自可读：lesson 显示正文/触发/解决，标签按真实类型，重要性仅数值行渲染', async () => {
    const wrapper = await mountMemory()
    // 检索结果渲染在「记忆检索」tab（第 5 个，n-tabs 惰性渲染，需先切换）
    const tabEls = wrapper.findAll('.n-tabs-tab')
    expect(tabEls.length).toBe(5)
    await tabEls[4].trigger('click')
    await flushPromises()

    const vm = wrapper.findComponent(MemoryPage).vm as unknown as {
      searchQuery: string
      searchMemory: () => Promise<void>
    }
    vm.searchQuery = '测试'
    await vm.searchMemory()
    await flushPromises()

    // 结果行为 border-l-4 卡片；逐行断言避免页头文案（“短期记忆、长期记忆…”）干扰
    const rows = wrapper.findAll('div.border-l-4')
    expect(rows).toHaveLength(3)
    const rowText = (i: number) => rows[i].text()
    const allRows = rows.map(r => r.text()).join('\n')

    // short_term 形状：内容 + 「短期」标签 + 数值重要性
    expect(rowText(0)).toContain('短期记忆内容')
    expect(rowText(0)).toContain('短期')
    expect(rowText(0)).toContain('重要性: 0.80')
    // insight 形状：content + 「洞察」标签，无重要性
    expect(rowText(1)).toContain('一条洞察')
    expect(rowText(1)).toContain('洞察')
    expect(rowText(1)).not.toContain('重要性')
    // lesson 形状：正文取 lesson 字段；trigger/solution 同行展示；「经验」标签
    expect(rowText(2)).toContain('教训内容')
    expect(rowText(2)).toContain('场景X')
    expect(rowText(2)).toContain('解决方案Y')
    expect(rowText(2)).toContain('经验')
    // 值域不谎报：无 long_term 项 → 结果区不出现「长期」；无 importance 的形状不渲染重要性
    expect(allRows).not.toContain('长期')
    expect(allRows.split('重要性').length - 1).toBe(1)
    expect(allRows).not.toContain('undefined')
  })
})
