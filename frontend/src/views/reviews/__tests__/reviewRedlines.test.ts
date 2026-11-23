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
  has_unparsed: true
}

function mountLedger(archived = false) {
  const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })
  return mount(
    {
      components: { RedlineLedger, NMessageProvider },
      template:
        '<n-message-provider><RedlineLedger case-id="case-1" :items="items" :archived="archived" /></n-message-provider>',
      data: () => ({ items: ITEMS, archived })
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

    await wrapper.find('[data-testid="redline-item-select-row-1"]').exists()
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
      item_id: null,
      evidence_id: null
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
