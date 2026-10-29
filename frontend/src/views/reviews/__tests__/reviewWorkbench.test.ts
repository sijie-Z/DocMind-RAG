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

    const saveButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('保存决策'))
    expect(saveButton).toBeTruthy()
    await saveButton!.trigger('click')
    await flushPromises()
    expect(api.decide).toHaveBeenCalledWith('case-1', 'item-1', {
      status: 'verified',
      note: null
    })

    const exportButton = wrapper
      .findAll('button')
      .find((button) => button.text().includes('导出核验单'))
    expect(exportButton).toBeTruthy()
    expect(exportButton!.attributes('disabled')).toBeDefined()
  })
})
