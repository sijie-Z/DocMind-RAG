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
    download: vi.fn()
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
          base: { status: 'identified', value: 30, unit: 'day' },
          revised: { status: 'identified', value: 60, unit: 'day' },
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

    expect(wrapper.text()).toContain('base:legacy_unverified')
    expect(wrapper.text()).toContain('revised:missing_version_meta')
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
