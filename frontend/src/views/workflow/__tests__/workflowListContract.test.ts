/**
 * B7 契约回归：工作流列表的 flow_data。
 *
 * 真值（backend/app/api/v1/endpoints/workflow.py:57-72）：列表项只有 6 键
 * （id/name/description/is_active/created_at/updated_at），**不含 flow_data**。
 *   - 旧卡片对每一行渲染「空白工作流」——对非空工作流是谎报；
 *   - 旧复制直接把 `item.flow_data`（undefined）传给 createWorkflow →
 *     副本恒为空壳（静默丢数据）。
 * 修复：预览无数据时只渲染中性占位图标（不声称）；复制先走 getWorkflow
 * 详情接口取真 flow_data 再 createWorkflow。
 *
 * 变异点：duplicateWorkflow 还原旧实现（flow_data: item.flow_data!）→ 用例 2 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import naive, { NMessageProvider, NDialogProvider } from 'naive-ui'
import { createI18n } from 'vue-i18n'

vi.mock('@/api/workflow', () => ({
  getWorkflows: vi.fn(),
  getWorkflow: vi.fn(),
  createWorkflow: vi.fn(),
  deleteWorkflow: vi.fn(),
}))

import WorkflowList from '../index.vue'
import { getWorkflows, getWorkflow, createWorkflow } from '@/api/workflow'

// T9：列表页主标题改引菜单键（视图接入 useI18n），挂载基座同步补 i18n 插件（空词条即可）。
const i18n = createI18n({
  legacy: false,
  locale: 'zh',
  missingWarn: false,
  fallbackWarn: false,
  messages: { zh: {} },
})

const LIST_ITEM = {
  id: 3,
  name: '流程A',
  description: '描述',
  is_active: true,
  created_at: '2026-10-01T00:00:00',
  updated_at: '2026-10-02T00:00:00',
}

const DETAIL = {
  ...LIST_ITEM,
  flow_data: {
    nodes: [{ id: 'input_1', type: 'input', position: { x: 0, y: 0 }, data: { label: '输入' } }],
    edges: [],
  },
}

const Host = defineComponent({
  setup() {
    return () =>
      h(NMessageProvider, null, {
        default: () => h(NDialogProvider, null, { default: () => h(WorkflowList) }),
      })
  },
})

async function mountList() {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/', component: { template: '<div />' } }],
  })
  await router.push('/')
  await router.isReady()
  const pinia = createPinia()
  setActivePinia(pinia)
  const wrapper = mount(Host, { global: { plugins: [naive, pinia, router, i18n] } })
  await flushPromises()
  return wrapper
}

describe('workflow 列表页 · flow_data 契约（B7）', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getWorkflows).mockResolvedValue({
      data: { data: { items: [LIST_ITEM], total: 1 } },
    } as never)
    vi.mocked(getWorkflow).mockResolvedValue({ data: { data: DETAIL } } as never)
    vi.mocked(createWorkflow).mockResolvedValue({ data: { data: { id: 9 } } } as never)
  })

  it('列表缺 flow_data 时不谎报「空白工作流」（旧文案对非空工作流恒误报）', async () => {
    const wrapper = await mountList()
    expect(wrapper.text()).toContain('流程A')
    expect(wrapper.text()).not.toContain('空白工作流')
  })

  it('复制走详情接口取真 flow_data（旧实现传 undefined → 副本恒空壳）', async () => {
    const wrapper = await mountList()
    const vm = wrapper.findComponent(WorkflowList).vm as unknown as {
      duplicateWorkflow: (_item: typeof LIST_ITEM) => Promise<void>
    }

    await vm.duplicateWorkflow(LIST_ITEM)
    await flushPromises()

    expect(getWorkflow).toHaveBeenCalledWith(3, { silent: true })
    expect(createWorkflow).toHaveBeenCalledWith({
      name: '流程A (副本)',
      description: '描述',
      flow_data: DETAIL.flow_data,
    }, { silent: true })
  })
})
