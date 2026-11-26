/**
 * 答案质量评测 UI 契约：历史列表 / 筛选参数 / 趋势 / 空态 / 详情失败态。
 *
 * 守护（每条都对应一个可变异点）：
 * 1. 列表按服务端返回渲染真实运行（模型、用例数、三维百分比）；
 * 2. 筛选只把状态/用户/日期交给服务端，用户筛选仅管理员可见；
 * 3. 趋势区渲染汇总、三维迷你折线与常驻数据表；失败点不显示分数；
 * 4. 无数据时给空态而不是空白表格；
 * 5. 失败运行详情显示 error_code 与「没有任何分数」，不渲染聚合分/逐条结果；
 * 6. 成功详情展示聚合、脱敏预览/理由/来源哈希、模型与提示词版本、返回入口。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import { NAlert, NButton, NIcon, NInputNumber, NPagination, NSelect, NSpin, NTag } from 'naive-ui'

const routerMocks = vi.hoisted(() => ({
  push: vi.fn(),
  paramsId: 'run-ok'
}))

const userState = vi.hoisted(() => ({ isAdmin: true }))

const apiMocks = vi.hoisted(() => ({
  list: vi.fn(),
  trend: vi.fn(),
  get: vi.fn(),
  create: vi.fn()
}))

vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    useRouter: () => ({ push: routerMocks.push }),
    useRoute: () => ({ params: { id: routerMocks.paramsId }, query: {} })
  }
})

vi.mock('@/stores/user', () => ({
  useUserStore: () => ({ isAdmin: userState.isAdmin })
}))

vi.mock('@/api/answerQualityEval', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/answerQualityEval')>()
  return { ...actual, answerQualityEvalApi: apiMocks }
})

import AnswerQualityRuns from '../answerQualityRuns.vue'
import AnswerQualityRunDetail from '../answerQualityRunDetail.vue'
import zh from '@/locales/zh'

const COMPLETED_RUN = {
  id: 'run-ok',
  organization_id: 1,
  user_id: 11,
  dataset_hash: 'a'.repeat(64),
  judge_model: 'deepseek-chat',
  prompt_version: 'rag-eval-v1',
  mode: 'judge' as const,
  status: 'completed' as const,
  case_count: 2,
  metrics: { faithfulness: 0.75, relevancy: 0.5, context_precision: 0.25 },
  error_code: null,
  created_by: 11,
  created_at: '2026-11-24T10:00:00Z',
  completed_at: '2026-11-24T10:00:05Z'
}

const FAILED_RUN = {
  ...COMPLETED_RUN,
  id: 'run-bad',
  dataset_hash: 'b'.repeat(64),
  status: 'failed' as const,
  metrics: { faithfulness: null, relevancy: null, context_precision: null },
  error_code: 'JUDGE_UNAVAILABLE',
  created_at: '2026-11-25T10:00:00Z'
}

const FAILED_DETAIL = {
  ...FAILED_RUN,
  aggregate: null,
  results: null
}

const SUCCESS_DETAIL = {
  ...COMPLETED_RUN,
  aggregate: {
    faithfulness: 0.75,
    relevancy: 0.5,
    context_precision: 0.25,
    cases_scored: 2,
    note: 'backend note'
  },
  results: [
    {
      case_id: 'c1',
      metrics: { faithfulness: 0.9, relevancy: 0.8, context_precision: 0.7 },
      reasons: { faithfulness: '与资料一致', relevancy: '切题' },
      source_refs: [{ ref_sha256: 'c'.repeat(64), masked_preview: '机密合同答…' }]
    },
    {
      case_id: 'c2',
      metrics: { faithfulness: 0.6, relevancy: 0.2, context_precision: 0 },
      reasons: {},
      source_refs: []
    }
  ]
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const NButtonStub = defineComponent({
  name: 'NButton',
  setup(_, { attrs, slots, emit }) {
    return () => h('button', { ...attrs, type: 'button', onClick: () => emit('click') }, slots.default?.())
  }
})

/** 日期选择器只需承接 v-model/attrs；测试直接改暴露的 dateRange。 */
const NDatePickerStub = defineComponent({
  name: 'NDatePicker',
  props: { value: { type: [Array, Number], default: null } },
  emits: ['update:value'],
  setup(_, { attrs }) {
    return () => h('div', attrs)
  }
})

const PAGE_GLOBAL = {
  plugins: [i18n],
  components: {
    NAlert,
    NButton,
    NDatePicker: NDatePickerStub,
    NIcon,
    NInputNumber,
    NPagination,
    NSelect,
    NSpin,
    NTag
  },
  stubs: { PageBreadcrumb: true, NButton: NButtonStub }
}

function mountList() {
  return mount(AnswerQualityRuns, { global: PAGE_GLOBAL })
}

function mountDetail() {
  return mount(AnswerQualityRunDetail, { global: PAGE_GLOBAL })
}

beforeEach(() => {
  vi.clearAllMocks()
  routerMocks.paramsId = 'run-ok'
  userState.isAdmin = true
  apiMocks.list.mockResolvedValue({
    data: {
      success: true,
      data: { items: [COMPLETED_RUN, FAILED_RUN], total: 2, page: 1, page_size: 20 }
    }
  })
  apiMocks.trend.mockResolvedValue({
    data: {
      success: true,
      data: {
        points: [
          { ...COMPLETED_RUN, metrics: { faithfulness: 0.7, relevancy: 0.6, context_precision: 0.5 } },
          { ...FAILED_RUN }
        ],
        summary: {
          runs: 2,
          succeeded_runs: 1,
          failed_runs: 1,
          averages: { faithfulness: 0.7, relevancy: 0.6, context_precision: 0.5 }
        }
      }
    }
  })
  apiMocks.get.mockResolvedValue({ data: { success: true, data: SUCCESS_DETAIL } })
})

describe('answer quality run history', () => {
  it('按服务端数据渲染运行、模型、用例数与三维百分比', async () => {
    const wrapper = mountList()
    await flushPromises()

    const ok = wrapper.get('[data-testid="answer-quality-row-run-ok"]')
    expect(ok.text()).toContain('deepseek-chat')
    expect(ok.text()).toContain('rag-eval-v1')
    expect(ok.text()).toContain('2')
    expect(ok.text()).toContain('75.0%')
    expect(ok.text()).toContain('50.0%')
    expect(ok.text()).toContain('25.0%')

    const bad = wrapper.get('[data-testid="answer-quality-row-run-bad"]')
    expect(bad.text()).toContain('失败')
    expect(bad.text()).toContain('JUDGE_UNAVAILABLE')
    expect(bad.text()).toContain('—')

    expect(apiMocks.list).toHaveBeenCalledWith({ page: 1, page_size: 20 })
    expect(apiMocks.trend).toHaveBeenCalledWith({ limit: 200 })
    expect(wrapper.text()).toContain('共 2 次运行')
  })

  it('筛选把状态/用户/日期交给服务端（日期为 UTC 日）', async () => {
    const wrapper = mountList()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      statusFilter: string
      userIdFilter: number | null
      dateRange: [number, number] | null
      applyFilter: () => void
    }
    vm.statusFilter = 'failed'
    vm.userIdFilter = 7
    vm.dateRange = [Date.UTC(2026, 10, 1), Date.UTC(2026, 10, 25)]
    vm.applyFilter()
    await flushPromises()

    expect(apiMocks.list).toHaveBeenLastCalledWith({
      status: 'failed',
      user_id: 7,
      date_from: '2026-11-01',
      date_to: '2026-11-25',
      page: 1,
      page_size: 20
    })
    expect(apiMocks.trend).toHaveBeenLastCalledWith({
      status: 'failed',
      user_id: 7,
      date_from: '2026-11-01',
      date_to: '2026-11-25',
      limit: 200
    })
  })

  it('普通成员看不到用户筛选，且不会把 user_id 发给服务端', async () => {
    userState.isAdmin = false
    const wrapper = mountList()
    await flushPromises()

    expect(wrapper.find('[data-testid="answer-quality-user-filter"]').exists()).toBe(false)
    expect(apiMocks.list).toHaveBeenCalledWith({ page: 1, page_size: 20 })
  })

  it('趋势区渲染汇总、折线与常驻表格；失败点显示 — 而不是 0 分', async () => {
    const wrapper = mountList()
    await flushPromises()

    const trend = wrapper.get('[data-testid="answer-quality-trend"]')
    expect(trend.text()).toContain('运行次数')
    expect(trend.text()).toContain('70.0%')
    expect(trend.text()).toContain('60.0%')
    expect(trend.text()).toContain('50.0%')
    expect(wrapper.find('[data-testid="answer-quality-sparkline-faithfulness"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="answer-quality-sparkline-relevancy"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="answer-quality-sparkline-context_precision"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="answer-quality-trend-row-run-bad"]').text()).toContain('—')
  })

  it('没有运行时展示空态', async () => {
    apiMocks.list.mockResolvedValue({
      data: { success: true, data: { items: [], total: 0, page: 1, page_size: 20 } }
    })
    apiMocks.trend.mockResolvedValue({
      data: {
        success: true,
        data: {
          points: [],
          summary: {
            runs: 0,
            succeeded_runs: 0,
            failed_runs: 0,
            averages: { faithfulness: null, relevancy: null, context_precision: null }
          }
        }
      }
    })
    const wrapper = mountList()
    await flushPromises()

    expect(wrapper.find('[data-testid="answer-quality-empty-state"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('还没有答案质量评测运行')
    expect(wrapper.find('[data-testid="answer-quality-trend"]').exists()).toBe(false)
  })
})

describe('answer quality run detail', () => {
  it('失败运行显示 error_code 与「没有分数」，不渲染任何分数或逐条结果', async () => {
    apiMocks.get.mockResolvedValue({ data: { success: true, data: FAILED_DETAIL } })
    routerMocks.paramsId = 'run-bad'
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.get('[data-testid="answer-quality-run-failed"]').text()).toContain('JUDGE_UNAVAILABLE')
    expect(wrapper.find('[data-testid="answer-quality-no-scores"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="answer-quality-aggregate"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="answer-quality-results"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('75.0%')
    // 失败运行仍保留模型/提示词版本，便于回查。
    expect(wrapper.get('[data-testid="answer-quality-judge-model"]').text()).toBe('deepseek-chat')
    expect(wrapper.get('[data-testid="answer-quality-prompt-version"]').text()).toBe('rag-eval-v1')
  })

  it('成功运行展示聚合指标、脱敏预览/理由/来源哈希与返回入口', async () => {
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.get('[data-testid="answer-quality-aggregate-faithfulness"]').text()).toContain('75.0%')
    expect(wrapper.get('[data-testid="answer-quality-aggregate-relevancy"]').text()).toContain('50.0%')
    expect(wrapper.get('[data-testid="answer-quality-aggregate-context_precision"]').text()).toContain('25.0%')
    expect(wrapper.get('[data-testid="answer-quality-aggregate"]').text()).toContain('参与计分用例')

    const row = wrapper.get('[data-testid="answer-quality-result-c1"]')
    expect(row.text()).toContain('机密合同答…')
    expect(row.text()).toContain('与资料一致')
    expect(row.text()).toContain('切题')
    expect(wrapper.get('[data-testid="answer-quality-result-c2"]').text()).toContain('评委未给出理由')
    expect(wrapper.get('[data-testid="answer-quality-result-c2"]').text()).toContain('无来源引用')

    expect(wrapper.get('[data-testid="answer-quality-judge-model"]').text()).toBe('deepseek-chat')
    expect(wrapper.get('[data-testid="answer-quality-prompt-version"]').text()).toBe('rag-eval-v1')

    await wrapper.get('[data-testid="answer-quality-back"]').trigger('click')
    expect(routerMocks.push).toHaveBeenCalledWith('/eval/answer-quality/runs')
  })
})
