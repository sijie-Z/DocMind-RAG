/**
 * Rubric 评测前端契约：评分表库 / 编辑为新版本 / diff / 运行 / 运行详情（校准面板）。
 *
 * 守护（每条都对应一个可变异点）：
 * 1. 库列表只请求活跃评分表并渲染状态/版本数；空态给创建与初始化出口；
 * 2. 创建只提交合法评分项（非法 key / 空问题被前端拦下，不发请求）；
 * 3. 详情：编辑=新增版本（旧版本行不被改写，提交为新版本请求）；
 * 4. diff 弹窗按基准/对比版本请求并逐类渲染；
 * 5. 运行弹窗：judge 模式明示「模型判定」；用例 JSON 非法时不发请求；
 * 6. 运行详情：聚合分、逐问题通过率、校准面板（一致率 + 混淆矩阵）；缺失金标
 *    时显示缺口状态而不是「100%」；失败运行显示机器码且没有分数；
 * 7. 页面不出现紫色系（check:no-purple 全量扫描之外的本文件兜底）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { defineComponent, h } from 'vue'
import { readFileSync } from 'node:fs'
import { resolve as resolvePath } from 'node:path'
import {
  NAlert,
  NButton,
  NIcon,
  NInput,
  NInputNumber,
  NMessageProvider,
  NModal,
  NPagination,
  NPopconfirm,
  NSelect,
  NSpin,
  NTag
} from 'naive-ui'

import RubricLibrary from '../rubricLibrary.vue'
import RubricDetail from '../rubricDetail.vue'
import RubricRunDetail from '../rubricRunDetail.vue'
import { rubricEvalApi } from '@/api/rubricEval'
import zh from '@/locales/zh'

const routerMocks = vi.hoisted(() => ({
  push: vi.fn(),
  paramsId: 'rub-1'
}))

vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    useRouter: () => ({ push: routerMocks.push }),
    useRoute: () => ({ params: { id: routerMocks.paramsId }, query: {} })
  }
})

vi.mock('@/api/rubricEval', () => ({
  rubricEvalApi: {
    list: vi.fn(),
    create: vi.fn(),
    bootstrap: vi.fn(),
    get: vi.fn(),
    update: vi.fn(),
    archive: vi.fn(),
    versions: vi.fn(),
    createVersion: vi.fn(),
    getVersion: vi.fn(),
    diff: vi.fn(),
    createRun: vi.fn(),
    listRuns: vi.fn(),
    getRun: vi.fn()
  }
}))

const api = vi.mocked(rubricEvalApi, true)

const ITEMS = [
  { key: 'grounded', question: '回答是否与资料一致？', weight: 3, strictness: 'neutral' },
  { key: 'cites', question: '是否给出可核对依据？', weight: 1, strictness: 'strict' }
]

const RUBRIC = {
  id: 'rub-1',
  organization_id: 1,
  name: '合同问答回答质量',
  description: '采购合同问答',
  status: 'active',
  latest_version: 2,
  created_by: 1,
  updated_by: 1,
  archived_at: null,
  archived_by: null,
  lock_version: 2,
  created_at: '2026-11-20T00:00:00Z',
  updated_at: '2026-11-22T00:00:00Z'
}

interface RubricVersionFixture {
  id: string
  rubric_id: string
  version_no: number
  item_count: number
  payload_sha256: string
  source: string
  change_note: string | null
  created_by: number
  created_at: string
  items: Array<{ key: string; question: string; weight: number; strictness: string }>
  intact: boolean
}

const V2: RubricVersionFixture = {
  id: 'ver-2',
  rubric_id: 'rub-1',
  version_no: 2,
  item_count: 2,
  payload_sha256: 'b'.repeat(64),
  source: 'manual',
  change_note: '提高 grounded 权重',
  created_by: 1,
  created_at: '2026-11-22T00:00:00Z',
  items: ITEMS,
  intact: true
}

const V1: RubricVersionFixture = {
  ...V2,
  id: 'ver-1',
  version_no: 1,
  source: 'bootstrap',
  change_note: null,
  payload_sha256: 'a'.repeat(64),
  items: [{ ...ITEMS[0], weight: 1 }, ITEMS[1]],
  created_at: '2026-11-20T00:00:00Z'
}

function summary(row: RubricVersionFixture) {
  const rest: Record<string, unknown> = { ...row }
  delete rest.items
  delete rest.intact
  return rest as Omit<RubricVersionFixture, 'items' | 'intact'>
}

const RUN_OK = {
  id: 'run-1',
  organization_id: 1,
  rubric_id: 'rub-1',
  rubric_version_no: 2,
  dataset_hash: 'c'.repeat(64),
  mode: 'judge',
  status: 'completed',
  case_count: 2,
  judge_model: 'deepseek-test',
  error_code: null,
  score: 0.75,
  calibration_available: true,
  created_by: 1,
  created_at: '2026-11-24T00:00:00Z',
  completed_at: '2026-11-24T00:01:00Z',
  aggregate: {
    score: 0.75,
    cases_scored: 2,
    questions: [
      {
        key: 'grounded',
        question: '回答是否与资料一致？',
        weight: 3,
        strictness: 'neutral',
        pass_rate: 1.0,
        mean_credit: 1.0,
        verdict_counts: { yes: 2, no: 0, unsure: 0 }
      },
      {
        key: 'cites',
        question: '是否给出可核对依据？',
        weight: 1,
        strictness: 'strict',
        pass_rate: 0.5,
        mean_credit: 0.5,
        verdict_counts: { yes: 1, no: 0, unsure: 1 }
      }
    ],
    calibration: {
      agreement_rate: 0.75,
      pairs_compared: 4,
      pairs_agreed: 3,
      cases_with_gold: 2,
      cases_total: 2,
      labels: ['yes', 'no', 'unsure'],
      confusion: {
        yes: { yes: 2, no: 0, unsure: 0 },
        no: { yes: 1, no: 0, unsure: 0 },
        unsure: { yes: 0, no: 0, unsure: 1 }
      },
      questions: [
        { key: 'grounded', compared: 2, agreed: 2, agreement_rate: 1.0 },
        { key: 'cites', compared: 2, agreed: 1, agreement_rate: 0.5 }
      ],
      note: '一致率仅覆盖带人工金标的用例'
    },
    note: 'rubric 得分是模型判定下的加权通过率'
  },
  results: [
    {
      case_id: 'c1',
      name: '好答案',
      answer: '答案一',
      score: 1.0,
      weight_total: 4,
      items: [
        { key: 'grounded', verdict: 'yes', credit: 1.0, weight: 3, strictness: 'neutral' },
        { key: 'cites', verdict: 'yes', credit: 1.0, weight: 1, strictness: 'strict' }
      ],
      gold_verdicts: { grounded: 'yes', cites: 'yes' }
    },
    {
      case_id: 'c2',
      name: '差答案',
      answer: '答案二',
      score: 0.5,
      weight_total: 4,
      items: [
        { key: 'grounded', verdict: 'yes', credit: 1.0, weight: 3, strictness: 'neutral' },
        { key: 'cites', verdict: 'unsure', credit: 0.0, weight: 1, strictness: 'strict' }
      ],
      gold_verdicts: { grounded: 'yes', cites: 'yes' }
    }
  ]
}

const RUN_NO_GOLD = {
  ...RUN_OK,
  id: 'run-2',
  calibration_available: false,
  aggregate: { ...RUN_OK.aggregate, calibration: null }
}

const RUN_FAILED = {
  ...RUN_OK,
  id: 'run-3',
  status: 'failed',
  error_code: 'JUDGE_UNAVAILABLE',
  score: null,
  aggregate: null,
  results: null
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const NButtonStub = defineComponent({
  name: 'NButton',
  setup(_, { attrs, slots, emit }) {
    return () => h('button', { ...attrs, type: 'button', onClick: () => emit('click') }, slots.default?.())
  }
})

const PAGE_GLOBAL = {
  plugins: [i18n],
  components: {
    NAlert,
    NButton,
    NIcon,
    NInput,
    NInputNumber,
    NMessageProvider,
    NModal,
    NPagination,
    NPopconfirm,
    NSelect,
    NSpin,
    NTag
  },
  stubs: { PageBreadcrumb: true, teleport: true, NButton: NButtonStub }
}

function mountWithProvider(component: unknown) {
  return mount(
    {
      components: { Page: component as never, NMessageProvider },
      template: '<n-message-provider><Page /></n-message-provider>'
    },
    { global: PAGE_GLOBAL }
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  routerMocks.paramsId = 'rub-1'
  api.list.mockResolvedValue({
    data: { success: true, data: { items: [RUBRIC], total: 1 } }
  } as never)
  api.get.mockResolvedValue({ data: { success: true, data: { ...RUBRIC, version: V2 } } } as never)
  api.versions.mockResolvedValue({
    data: {
      success: true,
      data: { items: [summary(V2), summary(V1)], total: 2, page: 1, page_size: 100, latest_version: 2 }
    }
  } as never)
  api.getVersion.mockImplementation((async (_id: string, versionNo: number) => ({
    data: { success: true, data: versionNo === 2 ? V2 : V1 }
  })) as never)
  api.listRuns.mockResolvedValue({
    data: { success: true, data: { items: [RUN_OK], total: 1 } }
  } as never)
  api.getRun.mockResolvedValue({ data: { success: true, data: RUN_OK } } as never)
  api.diff.mockResolvedValue({
    data: {
      success: true,
      data: {
        rubric_id: 'rub-1',
        base: { id: 'ver-1', version_no: 1, payload_sha256: 'a'.repeat(64), item_count: 2 },
        target: { id: 'ver-2', version_no: 2, payload_sha256: 'b'.repeat(64), item_count: 2 },
        identical: false,
        added: [],
        removed: [],
        changed: [
          {
            key: 'grounded',
            fields: { weight: { base: 1, target: 3 } }
          }
        ],
        unchanged_keys: ['cites'],
        summary: { added: 0, removed: 0, changed: 1, unchanged: 1 }
      }
    }
  } as never)
})

describe('评分表库', () => {
  it('只请求活跃评分表并渲染状态与版本数；空态给创建与初始化出口', async () => {
    const wrapper = mountWithProvider(RubricLibrary)
    await flushPromises()
    expect(api.list).toHaveBeenCalledWith({ status: 'active', page: 1, page_size: 20 })
    expect(wrapper.text()).toContain('合同问答回答质量')
    expect(wrapper.text()).toContain('2 个版本')
    expect(wrapper.find('[data-testid="rubric-row-rub-1"]').exists()).toBe(true)
    // 口径横幅必须写明「评答案、不是检索召回、judge 是模型判定」
    expect(wrapper.find('[data-testid="rubric-scope-banner"]').text()).toContain('不是检索召回')

    api.list.mockResolvedValue({ data: { success: true, data: { items: [], total: 0 } } } as never)
    const empty = mountWithProvider(RubricLibrary)
    await flushPromises()
    expect(empty.find('[data-testid="rubrics-empty-state"]').exists()).toBe(true)
    expect(empty.find('[data-testid="rubric-create-empty"]').exists()).toBe(true)
  })

  it('创建只提交合法评分项；非法 key 与空问题被前端拦下（不发请求）', async () => {
    api.create.mockResolvedValue({ data: { success: true, data: { id: 'rub-new' } } } as never)
    const wrapper = mountWithProvider(RubricLibrary)
    await flushPromises()
    await wrapper.find('[data-testid="rubric-create-open"]').trigger('click')
    await flushPromises()

    const vm = wrapper.findComponent(RubricLibrary).vm as unknown as {
      form: {
        name: string
        items: Array<{ key: string; question: string; weight: number; strictness: string }>
      }
      addItem: () => void
      submitCreate: () => Promise<void>
    }
    vm.form.name = '新评分表'
    vm.addItem()
    vm.form.items[0].key = 'BadKey'
    vm.form.items[0].question = '问题'
    await vm.submitCreate()
    expect(api.create).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="rubric-create-error"]').text()).toContain('key 规则')

    vm.form.items[0].key = 'grounded'
    vm.form.items[0].question = '   '
    await vm.submitCreate()
    expect(api.create).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="rubric-create-error"]').text()).toContain('问题')

    vm.form.items[0].question = '回答是否与资料一致？'
    vm.form.items[0].weight = 3
    vm.form.items[0].strictness = 'strict'
    await vm.submitCreate()
    expect(api.create).toHaveBeenCalledWith({
      name: '新评分表',
      description: null,
      items: [{ key: 'grounded', question: '回答是否与资料一致？', weight: 3, strictness: 'strict' }],
      change_note: null
    })
    expect(routerMocks.push).toHaveBeenCalledWith('/eval/rubrics/rub-new')
  })
})

describe('评分表详情：版本 / 新版本 / diff / 运行', () => {
  it('版本历史倒序，默认选中最新版并渲染评分项与严格度', async () => {
    const wrapper = mountWithProvider(RubricDetail)
    await flushPromises()
    expect(api.versions).toHaveBeenCalledWith('rub-1', { page: 1, page_size: 100 })
    expect(api.getVersion).toHaveBeenCalledWith('rub-1', 2)
    const list = wrapper.find('[data-testid="rubric-version-list"]')
    expect(list.text()).toContain('v2')
    expect(list.text()).toContain('v1')
    expect(list.text()).toContain('初始化')
    const table = wrapper.find('[data-testid="rubric-items-table"]')
    expect(table.text()).toContain('grounded')
    expect(table.text()).toContain('回答是否与资料一致？')
    expect(table.text()).toContain('严格')
    expect(wrapper.find('[data-testid="rubric-judge-disclaimer"]').text()).toContain('模型判定')
  })

  it('编辑为新版本：以当前版本项为起点，提交为新增版本（不就地改旧版本）', async () => {
    const vm0 = mountWithProvider(RubricDetail)
    await flushPromises()
    const vm = vm0.findComponent(RubricDetail).vm as unknown as {
      versionForm: { items: Array<{ key: string; question: string; weight: number; strictness: string }>; changeNote: string }
      openNewVersion: () => void
      submitNewVersion: () => Promise<void>
    }
    vm.openNewVersion()
    expect(vm.versionForm.items.map((i) => i.key)).toEqual(['grounded', 'cites'])
    vm.versionForm.items[0].weight = 5
    vm.versionForm.changeNote = '提高权重'
    api.createVersion.mockResolvedValue({ data: { success: true, data: { ...V2, version_no: 3 } } } as never)

    await vm.submitNewVersion()
    expect(api.createVersion).toHaveBeenCalledWith('rub-1', {
      items: [
        { key: 'grounded', question: '回答是否与资料一致？', weight: 5, strictness: 'neutral' },
        { key: 'cites', question: '是否给出可核对依据？', weight: 1, strictness: 'strict' }
      ],
      change_note: '提高权重'
    })
    expect(api.update).not.toHaveBeenCalled()
  })

  it('diff 弹窗按基准/对比版本请求，逐字段渲染变更', async () => {
    const wrapper = mountWithProvider(RubricDetail)
    await flushPromises()
    const vm = wrapper.findComponent(RubricDetail).vm as unknown as {
      openDiff: () => Promise<void>
      diffBase: number | null
      diffTarget: number | null
    }
    await vm.openDiff()
    await flushPromises()
    expect(api.diff).toHaveBeenCalledWith('rub-1', 1, 2)
    expect(wrapper.find('[data-testid="rubric-diff-summary"]').text()).toContain('变更 1')
    const changed = wrapper.find('[data-testid="rubric-diff-changed-grounded"]')
    expect(changed.text()).toContain('权重')
    expect(changed.text()).toContain('1 → 3')
  })

  it('运行：judge 明示模型判定；用例 JSON 非法时前端拦下（不发请求）', async () => {
    const wrapper = mountWithProvider(RubricDetail)
    await flushPromises()
    const vm = wrapper.findComponent(RubricDetail).vm as unknown as {
      openRun: () => void
      runForm: { casesJson: string; mode: string; versionNo: number | null }
      submitRun: () => Promise<void>
    }
    vm.openRun()
    await flushPromises()
    expect(vm.runForm.versionNo).toBe(2)
    expect(wrapper.find('[data-testid="rubric-run-mode-note"]').text()).toContain('模型判定')

    vm.runForm.casesJson = 'not json'
    await vm.submitRun()
    expect(api.createRun).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="rubric-run-error"]').text()).toContain('JSON')

    api.createRun.mockResolvedValue({ data: { success: true, data: { id: 'run-new' } } } as never)
    vm.runForm.casesJson = JSON.stringify([
      { case_id: 'c1', answer: '答案', gold_verdicts: { grounded: 'yes', cites: 'yes' } }
    ])
    await vm.submitRun()
    expect(api.createRun).toHaveBeenCalledWith('rub-1', {
      rubric_version_no: 2,
      mode: 'judge',
      cases: [
        {
          case_id: 'c1',
          name: null,
          answer: '答案',
          gold_verdicts: { grounded: 'yes', cites: 'yes' },
          fake_verdicts: null
        }
      ]
    })
    expect(routerMocks.push).toHaveBeenCalledWith('/eval/runs/run-new')
  })
})

describe('运行详情：聚合 / 校准 / 失败', () => {
  it('渲染聚合分、逐问题通过率、校准一致率与混淆矩阵', async () => {
    routerMocks.paramsId = 'run-1'
    const wrapper = mountWithProvider(RubricRunDetail)
    await flushPromises()
    expect(api.getRun).toHaveBeenCalledWith('run-1')
    expect(wrapper.find('[data-testid="rubric-run-score"]').text()).toContain('75.0%')
    const questions = wrapper.find('[data-testid="rubric-question-table"]')
    expect(questions.text()).toContain('grounded')
    expect(questions.text()).toContain('100.0%')
    expect(questions.text()).toContain('50.0%')
    expect(wrapper.find('[data-testid="rubric-calibration-panel"]').text()).toContain('评委校准')
    expect(wrapper.find('[data-testid="rubric-calibration-agreement"]').text()).toContain('75.0%')
    const matrix = wrapper.find('[data-testid="rubric-confusion-table"]')
    expect(matrix.exists()).toBe(true)
    expect(matrix.text()).toContain('不确定')
    expect(wrapper.find('[data-testid="rubric-result-c2"]').text()).toContain('cites: 不确定')
  })

  it('缺失人工金标：显示缺口状态，绝不展示成 100%', async () => {
    routerMocks.paramsId = 'run-2'
    api.getRun.mockResolvedValue({ data: { success: true, data: RUN_NO_GOLD } } as never)
    const wrapper = mountWithProvider(RubricRunDetail)
    await flushPromises()
    const panel = wrapper.find('[data-testid="rubric-calibration-panel"]')
    expect(panel.find('[data-testid="rubric-calibration-missing-gold"]').exists()).toBe(true)
    // 校准面板内不得出现一致率数字（缺失金标被渲染成「100% 准确」= 红）。
    expect(panel.find('[data-testid="rubric-calibration-agreement"]').exists()).toBe(false)
    expect(panel.text()).not.toContain('%')
    expect(panel.find('[data-testid="rubric-confusion-table"]').exists()).toBe(false)
  })

  it('失败运行：显示机器码与「没有分数」，不出现 0 分', async () => {
    routerMocks.paramsId = 'run-3'
    api.getRun.mockResolvedValue({ data: { success: true, data: RUN_FAILED } } as never)
    const wrapper = mountWithProvider(RubricRunDetail)
    await flushPromises()
    const failed = wrapper.find('[data-testid="rubric-run-failed"]')
    expect(failed.exists()).toBe(true)
    expect(failed.text()).toContain('JUDGE_UNAVAILABLE')
    expect(wrapper.find('[data-testid="rubric-run-score"]').text()).toContain('—')
    expect(wrapper.find('[data-testid="rubric-run-results"]').exists()).toBe(false)
  })
})

describe('设计系统', () => {
  it('三个视图源码不出现紫色系关键词', () => {
    for (const file of ['rubricLibrary.vue', 'rubricDetail.vue', 'rubricRunDetail.vue']) {
      const source = readFileSync(resolvePath(process.cwd(), 'src/views/eval', file), 'utf-8')
      expect(source).not.toMatch(/\b(purple|violet|indigo|fuchsia|pink)\b/i)
    }
  })
})
