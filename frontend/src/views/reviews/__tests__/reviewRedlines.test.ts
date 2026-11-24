/**
 * 合同改稿留痕台账组件契约：
 * 1. 初次挂载拉取提交列表，选中后加载台账明细；
 * 2. 上传走 reviewsApi.redlines.upload，成功后刷新列表/台账；
 * 3. 处置保存携带乐观锁版本与关联项，成功后刷新；
 * 4. 归档案件的输入控件全部禁用；
 * 5. 四语免责声明常驻，且本页绝不调用导出下载端点。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import {
  NAlert,
  NButton,
  NDataTable,
  NEmpty,
  NInput,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpin,
  NTag,
  NMessageProvider
} from 'naive-ui'

import RedlineLedger from '../components/RedlineLedger.vue'
import { reviewsApi } from '@/api/reviews'
import zh from '@/locales/zh'

vi.mock('@/api/reviews', () => ({
  reviewsApi: {
    redlines: {
      upload: vi.fn(),
      list: vi.fn(),
      get: vi.fn(),
      updateDisposition: vi.fn()
    },
    downloads: vi.fn(),
    download: vi.fn()
  }
}))

const api = vi.mocked(reviewsApi, true)

const SUBMISSION = {
  id: 'sub-1',
  case_id: 'case-1',
  sequence_no: 1,
  filename: 'contract-v2.docx',
  file_sha256: 'a'.repeat(64),
  file_size: 1024,
  parser_version: '1.0',
  status: 'ready',
  revision_count: 1,
  comment_count: 1,
  unparsed_count: 1,
  parse_warnings: [],
  uploaded_by: 1,
  created_at: '2026-11-24T00:00:00Z'
}

const ROW = {
  id: 'row-1',
  submission_id: 'sub-1',
  case_id: 'case-1',
  ordinal: 1,
  revision_type: 'insertion',
  pair_key: 'pair-1',
  before_text: '付款期限 30 天',
  after_text: '付款期限 60 天',
  before_sha256: 'b'.repeat(64),
  after_sha256: 'c'.repeat(64),
  author: 'legal-1',
  authored_at: '2026-11-24T00:00:00Z',
  comment_id: null,
  comment_author: 'legal-2',
  comment_date: null,
  location: { page: 2 },
  clause_no: ['8'],
  section_no: ['2.1'],
  unparsed_reason: null,
  text_provenance: 'parser_original',
  item_id: null,
  evidence_id: null,
  disposition: 'pending',
  note: null,
  disposition_by: null,
  disposition_at: null,
  lock_version: 3,
  created_at: '2026-11-24T00:00:00Z',
  updated_at: '2026-11-24T00:00:00Z'
}

const ITEMS = [
  {
    id: 'item-1',
    item_key: 'payment_term',
    ordinal: 1,
    change_type: 'modified',
    status: 'pending',
    evidence_count: 1,
    selected_evidence_count: 0,
    evidence: {
      policy: [],
      base: [{ id: 'ev-1' }],
      revised: []
    }
  }
] as never

const LEDGER = {
  items: [ROW],
  total: 1,
  page: 1,
  page_size: 20,
  counts: { revision: 1, comment: 1, unparsed: 1 },
  disclaimer: '只记录文本变化事实，不构成法律意见',
  has_unparsed: true,
  parse_warnings: [],
  has_truncation: false
}

const SUBMISSION_2 = { ...SUBMISSION, id: 'sub-2', case_id: 'case-2', sequence_no: 1 }
const ROW_2 = { ...ROW, id: 'row-2', case_id: 'case-2', submission_id: 'sub-2', before_text: '新的付款条款' }
const LEDGER_2 = { ...LEDGER, items: [ROW_2], total: 1 }

function mountLedger(archived = false) {
  const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })
  return mount(
    {
      components: { RedlineLedger, NMessageProvider },
      template:
        '<n-message-provider><RedlineLedger :case-id="caseId" :items="items" :archived="archived" /></n-message-provider>',
      data: () => ({ caseId: 'case-1', items: ITEMS, archived })
    },
    {
      global: {
        plugins: [i18n],
        components: { NAlert, NButton, NDataTable, NEmpty, NInput, NRadioButton, NRadioGroup, NSelect, NSpin, NTag }
      }
    }
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.redlines.list.mockResolvedValue({
    data: { success: true, data: { items: [SUBMISSION], total: 1 } }
  } as never)
  api.redlines.get.mockResolvedValue({ data: { success: true, data: LEDGER } } as never)
  api.redlines.upload.mockResolvedValue({ data: { success: true, data: SUBMISSION } } as never)
  api.redlines.updateDisposition.mockResolvedValue({
    data: { success: true, data: { ...ROW, disposition: 'reviewed', lock_version: 4 } }
  } as never)
})

describe('改稿留痕台账', () => {
  it('挂载后拉取列表与台账，展示 before→after、统计与免责声明', async () => {
    const wrapper = mountLedger()
    await flushPromises()

    expect(api.redlines.list).toHaveBeenCalledWith('case-1')
    expect(api.redlines.get).toHaveBeenCalledWith('case-1', 'sub-1', {
      page: 1,
      page_size: 20
    })
    const table = wrapper.find('[data-testid="redline-table"]')
    expect(table.exists()).toBe(true)
    expect(table.text()).toContain('付款期限 30 天')
    expect(table.text()).toContain('付款期限 60 天')
    expect(table.text()).toContain('legal-1')
    expect(table.text()).toContain('#8')

    // 未解析告警与计数必须来自台账响应，而不是硬编码或仅存在于 API 层
    const unparsedAlert = wrapper
      .findAllComponents(NAlert)
      .find((alert) => alert.text().includes(zh.reviews.redlines.hasUnparsed))
    expect(unparsedAlert).toBeDefined()
    expect(unparsedAlert!.props('type')).toBe('warning')
    expect(wrapper.text()).toContain(
      zh.reviews.redlines.counts.revision.replace('{count}', '1')
    )
    expect(wrapper.text()).toContain(
      zh.reviews.redlines.counts.comment.replace('{count}', '1')
    )
    expect(wrapper.text()).toContain(
      zh.reviews.redlines.counts.unparsed.replace('{count}', '1')
    )

    const disclaimer = wrapper.find('[data-testid="redline-disclaimer"]')
    expect(disclaimer.exists()).toBe(true)
    expect(disclaimer.text()).toContain(zh.reviews.redlines.disclaimerZh)
    expect(disclaimer.text()).toContain(zh.reviews.redlines.disclaimerEn)
    expect(disclaimer.text()).toContain(zh.reviews.redlines.disclaimerJa)
    expect(disclaimer.text()).toContain(zh.reviews.redlines.disclaimerFr)
  })

  it('上传文件走 redlines.upload，成功后刷新列表和台账；不调用下载端点', async () => {
    const wrapper = mountLedger()
    await flushPromises()

    const file = new File(['revision'], 'v2.docx')
    const input = wrapper.find('[data-testid="redline-upload"]')
    Object.defineProperty(input.element, 'files', { configurable: true, value: [file] })
    await input.trigger('change')
    await flushPromises()

    expect(api.redlines.upload).toHaveBeenCalledWith('case-1', file)
    expect(api.redlines.list).toHaveBeenCalledTimes(2)
    expect(api.redlines.get).toHaveBeenCalledTimes(2)
    expect(api.download).not.toHaveBeenCalled()
  })

  it('保存处置携带 expect_lock_version 与关联项，成功后重新加载', async () => {
    const wrapper = mountLedger()
    await flushPromises()

    const draftSelect = (testId: string) =>
      wrapper.findAllComponents(NSelect).find((s) => s.attributes('data-testid') === testId)
    const itemSelect = draftSelect('redline-item-select-row-1')
    const evidenceSelect = draftSelect('redline-evidence-select-row-1')
    expect(itemSelect).toBeDefined()
    expect(evidenceSelect).toBeDefined()
    itemSelect!.vm.$emit('update:value', 'item-1')
    evidenceSelect!.vm.$emit('update:value', 'ev-1')
    await flushPromises()

    const radio = wrapper.find('[data-testid="redline-disposition-row-1"] input[value="reviewed"]')
    expect(radio.exists()).toBe(true)
    await radio.setValue(true)
    const note = wrapper.find('[data-testid="redline-note-row-1"] input')
    await note.setValue('checked manually')
    await wrapper.find('[data-testid="redline-save-row-1"]').trigger('click')
    await flushPromises()

    expect(api.redlines.updateDisposition).toHaveBeenCalledWith('case-1', 'sub-1', 'row-1', {
      disposition: 'reviewed',
      note: 'checked manually',
      expected_lock_version: 3,
      item_id: 'item-1',
      evidence_id: 'ev-1'
    })
    expect(api.redlines.get).toHaveBeenCalledTimes(2)
  })

  it('筛选变化类型与处置状态触发带 query 的重新请求', async () => {
    const wrapper = mountLedger()
    await flushPromises()

    const selects = wrapper.findAllComponents(NSelect)
    // submission, revision_type, disposition + 每行的关联项/证据
    expect(selects.length).toBeGreaterThanOrEqual(3)
    selects[1].vm.$emit('update:value', 'comment')
    await flushPromises()
    expect(api.redlines.get).toHaveBeenLastCalledWith('case-1', 'sub-1', {
      revision_type: 'comment',
      page: 1,
      page_size: 20
    })

    const dispositionSelect = wrapper.find('[data-testid="redline-disposition-filter"]')
    expect(dispositionSelect.exists()).toBe(true)
    selects[2].vm.$emit('update:value', 'needs_clarification')
    await flushPromises()
    expect(api.redlines.get).toHaveBeenLastCalledWith('case-1', 'sub-1', {
      revision_type: 'comment',
      disposition: 'needs_clarification',
      page: 1,
      page_size: 20
    })
  })

  it('没有留痕记录时免责声明仍常驻', async () => {
    api.redlines.list.mockResolvedValueOnce({
      data: { success: true, data: { items: [], total: 0 } }
    } as never)
    const wrapper = mountLedger()
    await flushPromises()

    expect(wrapper.text()).toContain(zh.reviews.redlines.noSubmissions)
    const disclaimer = wrapper.find('[data-testid="redline-disclaimer"]')
    expect(disclaimer.exists()).toBe(true)
    expect(disclaimer.text()).toContain(zh.reviews.redlines.disclaimerZh)
  })

  it('表格分页接收 total，翻页后带新 page 重新加载台账', async () => {
    const wrapper = mountLedger()
    await flushPromises()

    const table = wrapper.findComponent(NDataTable)
    expect(table.props('pagination')).toMatchObject({
      page: 1,
      pageSize: 20,
      itemCount: 1
    })

    table.vm.$emit('update:page', 2)
    await flushPromises()

    expect(api.redlines.get).toHaveBeenLastCalledWith('case-1', 'sub-1', {
      page: 2,
      page_size: 20
    })
  })

  it('展示解析告警与截断提示', async () => {
    api.redlines.get.mockResolvedValueOnce({
      data: {
        success: true,
        data: {
          ...LEDGER,
          parse_warnings: ['row_cap_exceeded: 1000', 'text_cap_exceeded: 5000'],
          has_truncation: true
        }
      }
    } as never)
    const wrapper = mountLedger()
    await flushPromises()

    const warnings = wrapper.find('[data-testid="redline-parse-warnings"]')
    expect(warnings.exists()).toBe(true)
    expect(warnings.text()).toContain('row_cap_exceeded: 1000')
    expect(warnings.text()).toContain('text_cap_exceeded: 5000')
    const truncation = wrapper
      .findAllComponents(NAlert)
      .find((alert) => alert.text().includes(zh.reviews.redlines.hasTruncation))
    expect(truncation).toBeDefined()
    expect(truncation!.props('type')).toBe('error')
  })

  it('台账无未解析与截断标记时不展示告警', async () => {
    api.redlines.get.mockResolvedValueOnce({
      data: {
        success: true,
        data: { ...LEDGER, has_unparsed: false, parse_warnings: [], has_truncation: false }
      }
    } as never)
    const wrapper = mountLedger()
    await flushPromises()

    expect(wrapper.text()).not.toContain(zh.reviews.redlines.hasUnparsed)
    expect(wrapper.text()).not.toContain(zh.reviews.redlines.hasTruncation)
    expect(wrapper.find('[data-testid="redline-parse-warnings"]').exists()).toBe(false)
  })

  it('切换留痕提交后按新 submission_id 重新加载台账', async () => {
    api.redlines.list.mockResolvedValueOnce({
      data: {
        success: true,
        data: { items: [SUBMISSION, SUBMISSION_2], total: 2 }
      }
    } as never)
    api.redlines.get.mockImplementation(async (_caseId: string, submissionId: string) => ({
      data: {
        success: true,
        data: submissionId === 'sub-2' ? LEDGER_2 : LEDGER
      }
    }) as never)

    const wrapper = mountLedger()
    await flushPromises()
    expect(wrapper.text()).toContain('付款期限 30 天')

    const submissionSelect = wrapper
      .findAllComponents(NSelect)
      .find((s) => s.attributes('data-testid') === 'redline-submission-select')
    expect(submissionSelect).toBeDefined()
    submissionSelect!.vm.$emit('update:value', 'sub-2')
    await flushPromises()

    expect(api.redlines.get).toHaveBeenLastCalledWith('case-1', 'sub-2', {
      page: 1,
      page_size: 20
    })
    expect(wrapper.text()).toContain('新的付款条款')
    expect(wrapper.text()).not.toContain('付款期限 30 天')
  })

  it('caseId 变化时清空旧案件数据并重新拉取列表与台账', async () => {
    api.redlines.list.mockImplementation(async (caseId: string) => ({
      data: {
        success: true,
        data: {
          items: caseId === 'case-2' ? [SUBMISSION_2] : [SUBMISSION],
          total: 1
        }
      }
    }) as never)
    api.redlines.get.mockImplementation(async (caseId: string) => ({
      data: {
        success: true,
        data: caseId === 'case-2' ? LEDGER_2 : LEDGER
      }
    }) as never)

    const wrapper = mountLedger()
    await flushPromises()
    expect(wrapper.text()).toContain('付款期限 30 天')

    api.redlines.get.mockClear()
    await wrapper.setData({ caseId: 'case-2' })
    await flushPromises()

    expect(api.redlines.list).toHaveBeenLastCalledWith('case-2')
    expect(api.redlines.get).toHaveBeenCalledWith('case-2', 'sub-2', {
      page: 1,
      page_size: 20
    })
    expect(wrapper.text()).not.toContain('付款期限 30 天')
    expect(wrapper.text()).toContain('新的付款条款')
  })

  it('保存遇到 409 REVIEW_REDLINE_CONFLICT 时重新加载台账', async () => {
    api.redlines.updateDisposition.mockRejectedValueOnce({
      response: {
        status: 409,
        data: { success: false, error_code: 'REVIEW_REDLINE_CONFLICT', message: '红线台账批次冲突' }
      }
    })
    const wrapper = mountLedger()
    await flushPromises()
    expect(api.redlines.get).toHaveBeenCalledTimes(1)

    api.redlines.get.mockImplementationOnce(async () => ({
      data: {
        success: true,
        data: { ...LEDGER, items: [{ ...ROW, before_text: '新条款覆盖', lock_version: 4 }] }
      }
    }) as never)

    const radio = wrapper.find('[data-testid="redline-disposition-row-1"] input[value="reviewed"]')
    await radio.setValue(true)
    await wrapper.find('[data-testid="redline-note-row-1"] input').setValue('我的草稿')
    await wrapper.find('[data-testid="redline-save-row-1"]').trigger('click')
    await flushPromises()

    expect(api.redlines.get).toHaveBeenCalledTimes(2)
    // 刷新后必须呈现后端最新行，且清掉冲突前的草稿
    expect(wrapper.text()).toContain('新条款覆盖')
    expect((wrapper.find('[data-testid="redline-note-row-1"] input').element as HTMLInputElement).value).toBe('')
    expect(api.redlines.updateDisposition).toHaveBeenCalledWith(
      'case-1',
      'sub-1',
      'row-1',
      expect.objectContaining({ expected_lock_version: 3, note: '我的草稿' })
    )
  })

  it('保存遇到非 409 错误时展示后端文案且不重新加载台账', async () => {
    api.redlines.updateDisposition.mockRejectedValueOnce({
      response: {
        status: 422,
        data: { success: false, message: '处理备注超长' }
      }
    })
    const wrapper = mountLedger()
    await flushPromises()
    expect(api.redlines.get).toHaveBeenCalledTimes(1)

    const radio = wrapper.find('[data-testid="redline-disposition-row-1"] input[value="reviewed"]')
    await radio.setValue(true)
    await wrapper.find('[data-testid="redline-save-row-1"]').trigger('click')
    await flushPromises()

    expect(api.redlines.get).toHaveBeenCalledTimes(1)
  })

  it('归档案件：上传与处置控件全部禁用', async () => {
    const wrapper = mountLedger(true)
    await flushPromises()

    expect(wrapper.find('[data-testid="redline-upload"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="redline-upload-button"]').attributes('disabled')).toBeDefined()
    const save = wrapper.find('[data-testid="redline-save-row-1"]')
    expect(save.attributes('disabled')).toBeDefined()
    const radio = wrapper.find('[data-testid="redline-disposition-row-1"] input')
    expect(radio.attributes('disabled')).toBeDefined()
    const note = wrapper.find('[data-testid="redline-note-row-1"] input')
    expect(note.attributes('disabled')).toBeDefined()
  })
})
