/**
 * 合同核验工作台（/reviews/:id）契约测试。
 *
 * 守护三件事：
 * 1. 阻断案件必须显示原因码且禁用分析/决策；
 * 2. 决策/导出/下载走核验 API（review_evidence 快照），绝不请求 /documents/{id}/content；
 * 3. 未决导出 409 时给出显式提示。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NEmpty,
  NIcon,
  NInput,
  NMessageProvider,
  NPopconfirm,
  NRadioButton,
  NRadioGroup,
  NSpin,
  NTag
} from 'naive-ui'

import ReviewDetail from '../detail.vue'
import { reviewsApi } from '@/api/reviews'
import zh from '@/locales/zh'

vi.mock('vue-router', () => ({
  useRoute: () => ({ params: { id: 'case-1' } }),
  useRouter: () => ({ push: vi.fn() })
}))

vi.mock('@/api/reviews', () => ({
  reviewsApi: {
    list: vi.fn(),
    get: vi.fn(),
    analyze: vi.fn(),
    selectEvidence: vi.fn(),
    decide: vi.fn(),
    exportCase: vi.fn(),
    exports: vi.fn(),
    download: vi.fn(),
    archive: vi.fn()
  }
}))

const api = vi.mocked(reviewsApi)

function makeDetail(overrides: Record<string, unknown> = {}) {
  return {
    id: 'case-1',
    title: '采购合同 V1/V2 核验',
    status: 'in_review',
    checklist_version: 'v1',
    organization_id: 1,
    blocking_reasons: [],
    created_by: 7,
    lock_version: 3,
    documents: {
      policy: {
        document_id: 'p1',
        filename: 'policy.pdf',
        doc_role: 'policy',
        version_label: null,
        version_seq: null,
        content_sha256: 'p'.repeat(64),
        missing: false,
        identity_changed: false,
        readiness: { status: 'reviewable', blocking_reasons: [], warnings: [] }
      },
      base: {
        document_id: 'b1',
        filename: 'contract-v1.pdf',
        doc_role: 'contract',
        version_label: 'V1',
        version_seq: 1,
        content_sha256: 'b'.repeat(64),
        missing: false,
        identity_changed: false,
        readiness: { status: 'reviewable', blocking_reasons: [], warnings: [] }
      },
      revised: {
        document_id: 'r1',
        filename: 'contract-v2.pdf',
        doc_role: 'contract',
        version_label: 'V2',
        version_seq: 2,
        content_sha256: 'r'.repeat(64),
        missing: false,
        identity_changed: false,
        readiness: { status: 'reviewable', blocking_reasons: [], warnings: [] }
      }
    },
    items: [
      {
        id: 'item-1',
        item_key: 'payment_term',
        ordinal: 1,
        change_type: 'modified',
        status: 'pending',
        facts: {
          base: { status: 'identified', value: 30, unit: 'day', clause_no: [8] },
          revised: { status: 'identified', value: 60, unit: 'day', clause_no: [9] },
          policy: { status: 'not_found' }
        },
        decided_by: null,
        decided_at: null,
        decision_note: null,
        evidence_count: 2,
        selected_evidence_count: 0,
        evidence: {
          policy: [],
          base: [
            {
              id: 'ev-1',
              item_id: 'item-1',
              side: 'base',
              role: 'candidate',
              selected: false,
              document_id: 'b1',
              doc_role: 'contract',
              document_sha256: 'b'.repeat(64),
              chunk_id_at_capture: 'chunk-1',
              chunk_index: 0,
              clause_no: [8],
              section_no: [],
              page_number: 1,
              char_start: 0,
              char_end: 20,
              quote: '第八条 付款期限为30天。',
              quote_sha256: 'q'.repeat(64),
              text_provenance: 'parser_original',
              captured_at: '2026-10-30T00:00:00Z',
              stale: false
            }
          ],
          revised: []
        }
      }
    ],
    live_source_stale: false,
    stale_evidence_count: 0,
    ...overrides
  }
}

function mountDetail() {
  const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })
  return mount(
    {
      components: { ReviewDetail, NMessageProvider },
      template: '<n-message-provider><ReviewDetail /></n-message-provider>'
    },
    {
      global: {
        plugins: [i18n],
        // 生产由 app.use(naive) 全局注册；测试里注册本页用到的真组件。
        components: {
          NAlert,
          NButton,
          NEmpty,
          NIcon,
          NInput,
          NPopconfirm,
          NRadioButton,
          NRadioGroup,
          NSpin,
          NTag
        }
      }
    }
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.exports.mockResolvedValue({ data: { data: { items: [] } } } as never)
})

describe('合同核验工作台', () => {
  it('阻断案件显示原因码并禁用分析按钮；不请求文档正文接口', async () => {
    api.get.mockResolvedValue({
      data: {
        data: makeDetail({
          status: 'blocked',
          blocking_reasons: ['base:legacy_unverified', 'revised:missing_version_meta']
        })
      }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.text()).toContain('合同基准版：旧版分块未迁移')
    expect(wrapper.text()).toContain('合同修订版：未登记或未补全核验身份')
    expect(wrapper.text()).not.toContain('revised:missing_version_meta')
    // 原始码保留在横幅 title 属性里供排障
    expect(wrapper.find('[title*="base:legacy_unverified"]').exists()).toBe(true)
    const analyzeButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('执行分析'))
    expect(analyzeButton?.attributes('disabled')).toBeDefined()

    const calls = JSON.stringify([
      api.get.mock.calls,
      api.exports.mock.calls,
      api.analyze.mock.calls
    ])
    expect(calls).not.toContain('/documents/')
    expect(calls).not.toContain('/content')
  })

  it('决策与证据选择走核验 API，导出未决时提示', async () => {
    api.get.mockResolvedValue({ data: { data: makeDetail() } } as never)
    api.decide.mockResolvedValue({ data: { success: true } } as never)
    api.selectEvidence.mockResolvedValue({ data: { success: true } } as never)
    api.exportCase.mockRejectedValue({ response: { status: 409 } })

    const wrapper = mountDetail()
    await flushPromises()

    const selectButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('选为证据'))
    expect(selectButton).toBeTruthy()
    await selectButton!.trigger('click')
    await flushPromises()
    expect(api.selectEvidence).toHaveBeenCalledWith('case-1', 'item-1', {
      side: 'base',
      chunk_id: 'chunk-1',
      role: 'main',
      selected: true
    })

    // 未决策项不预选：保存按钮在人工选择前必须禁用（评审 F2）。
    const findSave = () =>
      wrapper.findAll('button').find((button) => button.text().includes('保存决策'))
    expect(findSave()?.attributes('disabled')).toBeDefined()

    const verifiedRadio = wrapper.find('input[type="radio"][value="verified"]')
    expect(verifiedRadio.exists()).toBe(true)
    await verifiedRadio.setValue(true)
    await flushPromises()
    const saveButton = findSave()
    expect(saveButton?.attributes('disabled')).toBeUndefined()
    await saveButton!.trigger('click')
    await flushPromises()
    expect(api.decide).toHaveBeenCalledWith('case-1', 'item-1', {
      status: 'verified',
      note: null
    })

    // 未识别事实必须如实展示（不得渲染成正常值）。
    expect(wrapper.text()).toContain('未识别')

    const exportButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('导出核验单'))
    expect(exportButton).toBeTruthy()
    expect(exportButton!.attributes('disabled')).toBeDefined()
  })
})


describe('合同核验工作台 · 状态边界', () => {
  it('completed 案件禁止再选择证据（与后端 SELECTABLE 状态一致）', async () => {
    api.get.mockResolvedValue({
      data: {
        data: makeDetail({
          status: 'completed',
          items: [
            {
              ...makeDetail().items[0],
              status: 'verified',
              decision_note: 'ok'
            }
          ]
        })
      }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()

    const selectButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('选为证据'))
    expect(selectButton).toBeTruthy()
    expect(selectButton!.attributes('disabled')).toBeDefined()
  })
})


describe('合同核验工作台 · 证据选择', () => {
  it('已选证据点击取消选择时发送 selected=false', async () => {
    const detail = makeDetail()
    detail.items[0].evidence.base[0].selected = true
    api.get.mockResolvedValue({ data: { data: detail } } as never)
    api.selectEvidence.mockResolvedValue({ data: { success: true } } as never)

    const wrapper = mountDetail()
    await flushPromises()

    const deselect = wrapper
      .findAll('button')
      .find((button) => button.text().includes('取消选择'))
    expect(deselect).toBeTruthy()
    await deselect!.trigger('click')
    await flushPromises()
    expect(api.selectEvidence).toHaveBeenCalledWith('case-1', 'item-1', {
      side: 'base',
      chunk_id: 'chunk-1',
      role: 'main',
      selected: false
    })
  })
})


describe('合同核验工作台 · 导出与草稿', () => {
  it('差异条展示基准→修订，条款定位来自文本事实', async () => {
    api.get.mockResolvedValue({ data: { data: makeDetail() } } as never)
    const wrapper = mountDetail()
    await flushPromises()

    const diff = wrapper.find('[data-testid="item-diff"]')
    expect(diff.exists()).toBe(true)
    expect(diff.text()).toContain('30 自然日')
    expect(diff.text()).toContain('60 自然日')
    expect(diff.text()).toContain('→')
    const locator = wrapper.find('[data-testid="item-locator"]')
    expect(locator.text()).toContain('第 8 条')
    expect(locator.text()).toContain('第 9 条')
  })

  it('未保存备注会禁用导出并提示先保存', async () => {
    const base = makeDetail()
    api.get.mockResolvedValue({
      data: {
        data: makeDetail({
          items: [{ ...base.items[0], status: 'verified', decision_note: 'ok' }]
        })
      }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()

    const findExport = () =>
      wrapper.findAll('button').find((button) => button.text().includes('导出核验单'))
    expect(findExport()?.attributes('disabled')).toBeUndefined()

    await wrapper.find('.review-decision-note input').setValue('改了备注但没保存')
    await flushPromises()

    expect(findExport()?.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('有未保存的决策')
  })

  it('刷新保留未保存草稿（备注不丢）', async () => {
    api.get.mockResolvedValue({ data: { data: makeDetail() } } as never)
    const wrapper = mountDetail()
    await flushPromises()

    const input = wrapper.find('.review-decision-note input')
    await input.setValue('待确认的备注')
    const refresh = wrapper.findAll('button').find((button) => button.text().includes('重试'))
    expect(refresh).toBeTruthy()
    await refresh!.trigger('click')
    await flushPromises()

    expect(
      (wrapper.find('.review-decision-note input').element as HTMLInputElement).value
    ).toBe('待确认的备注')
  })

  it('横幅「下一项未决策」跳过已决策项并切到未决策筛选', async () => {
    const base = makeDetail()
    api.get.mockResolvedValue({
      data: {
        data: makeDetail({
          items: [
            { ...base.items[0], status: 'verified', decision_note: 'ok' },
            { ...base.items[0], id: 'item-2', item_key: 'start_condition', ordinal: 2 }
          ]
        })
      }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()

    const next = wrapper.find('[data-testid="review-next-pending"]')
    expect(next.exists()).toBe(true)
    await next.trigger('click')
    await flushPromises()

    // 点击后切到「未决策」筛选：对应 chip 的 aria-pressed 变为 true。
    const pendingChip = wrapper
      .findAll('button')
      .find((button) => button.text().includes('未决策'))
    expect(pendingChip?.attributes('aria-pressed')).toBe('true')
  })

  it('全部决策后导出触发 exportCase；409 时展示未决项（不静默）', async () => {
    const base = makeDetail()
    const detail = makeDetail({
      items: [{ ...base.items[0], status: 'verified', decision_note: 'ok' }]
    })
    api.get.mockResolvedValue({ data: { data: detail } } as never)
    api.exportCase.mockRejectedValue({
      response: {
        status: 409,
        data: { detail: { items: [{ item_key: 'payment_term', reasons: ['undecided'] }] } }
      }
    })

    const wrapper = mountDetail()
    await flushPromises()

    const exportButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('导出核验单'))
    expect(exportButton).toBeTruthy()
    expect(exportButton!.attributes('disabled')).toBeUndefined()
    await exportButton!.trigger('click')
    await flushPromises()
    expect(api.exportCase).toHaveBeenCalledWith('case-1')

    // 409 提示本地化：item 业务名 + 原因文案，且不再出现机器码
    const toast = document.body.textContent || ''
    expect(toast).toContain('付款期限')
    expect(toast).toContain('存在未决核验项')
    expect(toast).not.toContain('undecided')
    expect(toast).not.toContain('payment_term')
  })

  it('歧义事实走原因码注册表：组合码本地化、未知码透传', async () => {
    const base = makeDetail()
    api.get.mockResolvedValue({
      data: {
        data: makeDetail({
          items: [
            {
              ...base.items[0],
              facts: {
                base: { status: 'ambiguous', reason: 'unit_unclear+multiple_durations' },
                revised: { status: 'ambiguous', reason: 'some_future_code' },
                policy: { status: 'not_found' }
              }
            }
          ]
        })
      }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.text()).toContain('单位无法判定')
    expect(wrapper.text()).toContain('多个期限候选')
    expect(wrapper.text()).toContain('some_future_code')
  })

  it('顺带刷新（选证据）保留未保存的决策草稿', async () => {
    api.get.mockResolvedValue({ data: { data: makeDetail() } } as never)
    api.selectEvidence.mockResolvedValue({ data: { success: true } } as never)

    const wrapper = mountDetail()
    await flushPromises()

    const noteInput = wrapper
      .findAll('input')
      .find((input) => input.attributes('placeholder') === '决策说明')
    expect(noteInput).toBeTruthy()
    await noteInput!.setValue('尚未保存的草稿')

    const selectButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('选为证据'))
    await selectButton!.trigger('click')
    await flushPromises()

    const refreshedInput = wrapper
      .findAll('input')
      .find((input) => input.attributes('placeholder') === '决策说明')
    expect((refreshedInput!.element as HTMLInputElement).value).toBe('尚未保存的草稿')
  })
})


describe('归档出口（Batch 7）', () => {
  it('陈旧页归档遇 409 ALREADY_ARCHIVED 自动收敛到只读态', async () => {
    const archived = makeDetail({
      status: 'completed',
      completed_at: '2026-10-30T00:00:00Z',
      completed_by: 7,
      archived_at: '2026-10-30T00:00:00Z',
      archived_by: 1
    })
    api.get
      .mockResolvedValueOnce({
        data: { data: makeDetail({ status: 'blocked', blocking_reasons: ['base:legacy'] }) }
      } as never)
      .mockResolvedValue({ data: { data: archived } } as never)
    api.archive.mockRejectedValue({
      response: { status: 409, data: { error_code: 'REVIEW_CASE_ALREADY_ARCHIVED' } }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()

    const vm = wrapper.findComponent(ReviewDetail).vm as unknown as {
      archiveCase: () => Promise<void>
    }
    await vm.archiveCase()
    await flushPromises()

    expect(api.get, '409 后必须重新拉取收敛').toHaveBeenCalledTimes(2)
    expect(wrapper.find('[data-testid="archived-banner"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="archive-case"]').exists()).toBe(false)
  })

  const baseDetail = (overrides: Record<string, unknown> = {}) =>
    makeDetail({
      status: 'completed',
      completed_at: '2026-10-30T00:00:00Z',
      completed_by: 7,
      ...overrides
    })

  const completedDetail = (overrides: Record<string, unknown> = {}) => {
    const detail = baseDetail(overrides)
    detail.items = detail.items.map((item) => ({
      ...item,
      status: 'verified',
      decided_by: 7,
      decided_at: '2026-10-30T00:00:00Z',
      decision_note: '已核对'
    })) as unknown as typeof detail.items
    return detail
  }

  it('blocked 案件显示归档入口；popconfirm 确认后调用归档接口并刷新', async () => {
    api.get.mockResolvedValue({
      data: {
        data: makeDetail({ status: 'blocked', blocking_reasons: ['base:legacy_unverified'] })
      }
    } as never)
    api.archive.mockResolvedValue({ data: { data: completedDetail() } } as never)
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.find('[data-testid="archive-case"]').exists()).toBe(true)
    // 走真实接线：NPopconfirm 的 positive-click → archiveCase（删掉绑定会红）
    wrapper.findComponent(NPopconfirm).vm.$emit('positive-click')
    await flushPromises()

    expect(api.archive).toHaveBeenCalledWith('case-1')
    expect(api.get).toHaveBeenCalledTimes(2)
  })

  it('completed（未归档）案件也有归档入口，且控件处于可编辑态', async () => {
    api.get.mockResolvedValue({ data: { data: completedDetail() } } as never)
    const wrapper = mountDetail()
    await flushPromises()

    expect(wrapper.find('[data-testid="archive-case"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="archived-banner"]').exists()).toBe(false)
    const buttons = wrapper.findAll('button')
    const exportBtn = buttons.find((b) => b.text().includes('导出核验单'))
    expect(exportBtn?.attributes('disabled')).toBeUndefined()
    expect(wrapper.find('input[type="radio"]').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('input[placeholder="决策说明"]').attributes('disabled')).toBeUndefined()
  })

  it('已归档案件：只读横幅、无归档入口，决策/备注/导出真实禁用', async () => {
    api.get.mockResolvedValue({
      data: { data: completedDetail({ archived_at: '2026-10-30T00:00:00Z', archived_by: 1 }) }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()
    const vm = wrapper.findComponent(ReviewDetail).vm as unknown as {
      decisions: Record<string, { status: string | null; note: string }>
    }
    // 制造草稿改动：没有归档冻结时，保存按钮本应可用
    vm.decisions['item-1'].status = 'needs_human'
    await flushPromises()

    expect(wrapper.find('[data-testid="archived-banner"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('该案件已归档（只读）')
    expect(wrapper.find('[data-testid="archive-case"]').exists()).toBe(false)

    const buttons = wrapper.findAll('button')
    const exportBtn = buttons.find((b) => b.text().includes('导出核验单'))
    expect(exportBtn?.attributes('disabled')).toBeDefined()
    const saveBtn = buttons.find((b) => b.text().includes('保存决策'))
    expect(saveBtn?.attributes('disabled')).toBeDefined()
    expect(wrapper.find('input[type="radio"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('input[placeholder="决策说明"]').attributes('disabled')).toBeDefined()
  })

  it('ready/in_review 案件不显示归档入口', async () => {
    api.get.mockResolvedValue({ data: { data: makeDetail() } } as never)
    const wrapper = mountDetail()
    await flushPromises()
    expect(wrapper.find('[data-testid="archive-case"]').exists()).toBe(false)
  })

  it('结论横幅：状态、真实进度与差异计数来自详情 items', async () => {
    api.get.mockResolvedValue({ data: { data: makeDetail() } } as never)
    const wrapper = mountDetail()
    await flushPromises()
    const banner = wrapper.find('[data-testid="review-conclusion"]')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('核验中')
    expect(banner.text()).toContain('已处理 0/1 项')
    expect(banner.text()).toContain('有变更 1')
    expect(banner.text()).toContain('未决策')
  })

  it('completed 只由案件状态决定：全部已决策但未导出显示「待导出」', async () => {
    const base = makeDetail()
    api.get.mockResolvedValue({
      data: { data: makeDetail({ items: [{ ...base.items[0], status: 'verified' }] }) }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()
    const banner = wrapper.find('[data-testid="review-conclusion"]')
    expect(banner.text()).toContain('待导出')
    expect(banner.text()).not.toContain('核验完成')
  })

  it('completed 案件显示「核验完成」（即使仍有未决策项）', async () => {
    api.get.mockResolvedValue({ data: { data: makeDetail({ status: 'completed' }) } } as never)
    const wrapper = mountDetail()
    await flushPromises()
    const banner = wrapper.find('[data-testid="review-conclusion"]')
    expect(banner.text()).toContain('核验完成')
  })

  it('计数 chip 可过滤核验项', async () => {
    const base = makeDetail()
    const second = {
      ...base.items[0],
      id: 'item-2',
      item_key: 'start_condition',
      ordinal: 2,
      change_type: 'unchanged',
      status: 'verified'
    }
    api.get.mockResolvedValue({
      data: { data: makeDetail({ items: [base.items[0], second] }) }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()
    expect(wrapper.findAll('[data-testid^="review-item-"]').length).toBe(2)

    const decidedChip = wrapper
      .findAll('button')
      .find((button) => button.text().includes('已确认'))
    expect(decidedChip).toBeTruthy()
    await decidedChip!.trigger('click')
    const items = wrapper.findAll('[data-testid^="review-item-"]')
    expect(items.length).toBe(1)
    expect(items[0].text()).toContain('起算条件')
  })

  it('ready + 已分析且无异常 → 待决策；ready + unidentified → 需人工复核（不被 ready 分支吞掉）', async () => {
    const base = makeDetail()
    api.get.mockResolvedValue({ data: { data: makeDetail({ status: 'ready' }) } } as never)
    const clean = mountDetail()
    await flushPromises()
    expect(clean.find('[data-testid="review-conclusion"]').text()).toContain('待决策')

    api.get.mockResolvedValue({
      data: {
        data: makeDetail({
          status: 'ready',
          items: [{ ...base.items[0], change_type: 'unidentified', status: 'pending' }]
        })
      }
    } as never)
    const unidentified = mountDetail()
    await flushPromises()
    const banner = unidentified.find('[data-testid="review-conclusion"]').text()
    expect(banner).toContain('需人工复核')
    expect(banner).toContain('无法判定')
    expect(banner).not.toContain('待决策')
  })

  it('completed 仍在横幅中如实披露待人工/无法判定计数', async () => {
    const base = makeDetail()
    api.get.mockResolvedValue({
      data: {
        data: makeDetail({
          status: 'completed',
          items: [
            { ...base.items[0], status: 'needs_human', change_type: 'modified' },
            { ...base.items[0], id: 'item-2', item_key: 'start_condition', ordinal: 2, status: 'pending', change_type: 'unidentified' }
          ]
        })
      }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()
    const banner = wrapper.find('[data-testid="review-conclusion"]').text()
    expect(banner).toContain('核验完成')
    expect(banner).toContain('待人工确认')
    expect(banner).toContain('无法判定')
  })

  it('版本条默认折叠，展开后显示文件名', async () => {
    api.get.mockResolvedValue({ data: { data: makeDetail() } } as never)
    const wrapper = mountDetail()
    await flushPromises()
    expect(wrapper.text()).not.toContain('contract-v1.pdf')
    await wrapper.find('[data-testid="review-versions-toggle"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('contract-v1.pdf')
  })
})
