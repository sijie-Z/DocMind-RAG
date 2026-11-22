/** 运行历史页判别用例：服务端过滤 + error 行 null + replay payload + 时间轴。 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'

const apiMocks = vi.hoisted(() => ({
  listEvalRuns: vi.fn(),
  getEvalTrend: vi.fn(),
  getEvalRun: vi.fn(),
  exportEvalRun: vi.fn(),
  runEval: vi.fn()
}))

vi.mock('@/api/retrievalEval', () => apiMocks)
vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() })
}))

import EvalRunHistory from '../evalRunHistory.vue'
import zh from '@/locales/zh'

const RUN_A = {
  id: 'run-a',
  organization_id: 1,
  created_by: 11,
  top_k: 5,
  case_ids: ['case-1'],
  case_scope: 'subset',
  case_set_hash: 'a'.repeat(64),
  counts: { cases: 1, evaluated: 1, errors: 0 },
  metrics: { recall_at_k: 0.5, mrr: 0.5, document_hit_at_k: 0.5, hit_at_k: 0.5 },
  error_case_ids: [],
  created_at: '2026-11-23T10:00:00'
}
const RUN_B = { ...RUN_A, id: 'run-b', case_set_hash: 'b'.repeat(64), counts: { cases: 1, evaluated: 0, errors: 1 }, metrics: { recall_at_k: null, mrr: null, document_hit_at_k: null, hit_at_k: null }, error_case_ids: ['case-1'] }

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

// 真按钮桩：让 wrapper.trigger('click') 真正触发 @click（stub=true 只渲染占位标签）。
const NButtonStub = defineComponent({
  name: 'NButton',
  setup(_, { attrs, slots, emit }) {
    return () => h('button', { ...attrs, type: 'button', onClick: () => emit('click') }, slots.default?.())
  }
})

async function mountView(query: Record<string, string> = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/knowledge/eval-runs', component: defineComponent({ render: () => h('div') }) },
      { path: '/knowledge', component: defineComponent({ render: () => h('div') }) }
    ]
  })
  await router.push({ path: '/knowledge/eval-runs', query })
  await router.isReady()
  const Host = defineComponent({
    render: () => h(NMessageProvider, null, { default: () => h(EvalRunHistory) })
  })
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n, router],
      stubs: { PageBreadcrumb: true, NDataTable: true, NSelect: true, NButton: NButtonStub }
    }
  })
  await flushPromises()
  return { wrapper, router }
}

beforeEach(() => {
  apiMocks.listEvalRuns.mockReset()
  apiMocks.getEvalTrend.mockReset()
  apiMocks.getEvalRun.mockReset()
  apiMocks.exportEvalRun.mockReset()
  apiMocks.runEval.mockReset()
  apiMocks.listEvalRuns.mockResolvedValue({ data: { success: true, data: { data: [RUN_A, RUN_B], total: 2 } } })
  apiMocks.getEvalTrend.mockResolvedValue({
    data: {
      success: true,
      data: {
        points: [RUN_A, RUN_B],
        summary: { runs: 2, succeeded_runs: 1, failed_runs: 1, averages: { recall_at_k: 0.5, mrr: 0.5, document_hit_at_k: 0.5, hit_at_k: 0.5 } }
      }
    }
  })
  apiMocks.getEvalRun.mockResolvedValue({
    data: {
      success: true,
      data: {
        ...RUN_A,
        results: [
          { case_id: 'case-ok', status: 'ok', name: 'ok', query: 'q-ok', recall_at_k: 0.5 },
          { case_id: 'case-err', status: 'error', name: 'err', query: 'q-err', error_code: 'RETRIEVAL_ERROR', recall_at_k: null }
        ]
      }
    }
  })
})

describe('eval run history view', () => {
  it('把 case_set_hash 交给服务端列表 API，不再客户端二次过滤', async () => {
    // 服务端只返回命中 hash 的一页；若前端再做过滤/截断，总数展示会漂移。
    apiMocks.listEvalRuns.mockResolvedValue({ data: { success: true, data: { data: [RUN_A], total: 1 } } })
    const { wrapper } = await mountView({ case_set_hash: RUN_A.case_set_hash })
    expect(apiMocks.listEvalRuns).toHaveBeenCalledWith({
      page: 1,
      page_size: 200,
      case_set_hash: RUN_A.case_set_hash
    })
    expect(apiMocks.getEvalTrend).toHaveBeenCalledWith({ case_set_hash: RUN_A.case_set_hash, limit: 30 })
    expect(wrapper.text()).toContain('显示 1/1 次运行')
  })

  it('error 行 recall 为 null 时显示 —，不伪造成 0.0%', async () => {
    const { wrapper } = await mountView({ run: RUN_A.id })
    const detail = wrapper.find('[data-test="eval-run-detail"]')
    expect(detail.exists()).toBe(true)
    const rows = detail.findAll('tbody tr')
    expect(rows).toHaveLength(2)
    const cellText = (rowIndex: number) => rows[rowIndex].findAll('td')[3].text().trim()
    // 成功行 0.5 → 50.0%；失败行后端 recall_at_k=null → 必须显示 em dash。
    expect(cellText(0)).toBe('50.0%')
    expect(cellText(1)).toBe('—')
  })

  it('点击重跑按钮：用原 top_k/case_ids 且显式 persist=true', async () => {
    apiMocks.runEval.mockResolvedValue({ data: { success: true, data: { run_id: 'run-new' } } })
    const { wrapper } = await mountView({ run: RUN_A.id })
    const replay = wrapper.find('[data-test="eval-replay"]')
    expect(replay.exists()).toBe(true)
    await replay.trigger('click')
    await flushPromises()
    expect(apiMocks.runEval).toHaveBeenCalledTimes(1)
    expect(apiMocks.runEval).toHaveBeenCalledWith({
      case_ids: ['case-1'],
      top_k: 5,
      persist: true
    })
  })

  it('趋势图按时间定位而非按序号等距', async () => {
    const base = '2026-11-23T'
    const points = [10, 11, 21].map((hour, index) => ({
      ...RUN_A,
      id: `run-${index}`,
      created_at: `${base}${String(hour).padStart(2, '0')}:00:00`,
      metrics: { ...RUN_A.metrics, recall_at_k: 0.5 + index * 0.1 }
    }))
    apiMocks.getEvalTrend.mockResolvedValue({
      data: {
        success: true,
        data: {
          points,
          summary: { runs: 3, succeeded_runs: 3, failed_runs: 0, averages: { recall_at_k: 0.6, mrr: 0.6, document_hit_at_k: 0.6, hit_at_k: 0.6 } }
        }
      }
    })
    const { wrapper } = await mountView()
    const dots = wrapper.findAll('circle.eval-dot')
    expect(dots).toHaveLength(3)
    const xs = dots.map((dot) => Number(dot.attributes('cx')))
    // 10/11/21 点：时间轴下第二点约在 15%，序号轴下会在 50%。
    expect(xs[1]).toBeLessThan(160)
    expect(xs[2]).toBeGreaterThan(600)
    expect(wrapper.find('[data-test="eval-trend-table"]').exists()).toBe(true)
  })

  it('提供数据表视图（趋势值不被悬停门控）', async () => {
    const { wrapper } = await mountView()
    expect(wrapper.find('[data-test="eval-trend-table"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="eval-trend-chart"]').exists()).toBe(true)
  })
})
