/**
 * 新建核验（建案弹窗）契约测试。
 *
 * 守护四件事：
 * 1. 打开弹窗才加载候选文档；未登记身份的文档有显式标记与登记入口；
 * 2. 选满三份文档后按需做 readiness 预检，受限文档显示原因码；
 * 3. 建案载荷 = 三份文档 id + 标题 + checklist v1，成功后跳详情；
 * 4. 三份文档重复时不提交；合同身份登记缺少版本族/标签/序时不提交。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import {
  NButton,
  NDataTable,
  NEmpty,
  NMessageProvider,
  NPagination,
  NSelect,
  NSpin,
  NTag
} from 'naive-ui'

import ReviewsIndex from '../index.vue'
import CreateCaseModal from '../components/CreateCaseModal.vue'
import { reviewsApi } from '@/api/reviews'
import zh from '@/locales/zh'

const push = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({ push })
}))

vi.mock('@/api/reviews', () => ({
  reviewsApi: {
    list: vi.fn(),
    candidates: vi.fn(),
    createCase: vi.fn(),
    readiness: vi.fn(),
    putDocumentMeta: vi.fn()
  }
}))

const api = vi.mocked(reviewsApi)

const POLICY = {
  document_id: 'p1',
  filename: '制度.pdf',
  status: 'indexed',
  meta: { doc_role: 'policy' }
}
const BASE = {
  document_id: 'b1',
  filename: '合同V1.pdf',
  status: 'indexed',
  meta: { doc_role: 'contract', family_key: 'fam', version_label: 'V1', version_seq: 1 }
}
const REVISED = {
  document_id: 'r1',
  filename: '合同V2.pdf',
  status: 'indexed',
  meta: { doc_role: 'contract', family_key: 'fam', version_label: 'V2', version_seq: 2 }
}
const NOMETA = { document_id: 'n1', filename: '未登记.pdf', status: 'indexed', meta: null }

function mountIndex() {
  const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })
  return mount(
    {
      components: { ReviewsIndex, NMessageProvider },
      template: '<n-message-provider><ReviewsIndex /></n-message-provider>'
    },
    {
      global: {
        plugins: [i18n],
        components: { NButton, NDataTable, NEmpty, NPagination, NSelect, NSpin, NTag }
      }
    }
  )
}

type ModalVm = {
  form: { title: string }
  sideSelection: Record<'policy' | 'base' | 'revised', string | null>
  candidates: typeof POLICY[]
  readiness: Record<string, { status: string; blocking_reasons: string[] } | undefined>
  optionLabel: (candidate: unknown) => string
  openMeta: (side: 'policy' | 'base' | 'revised') => void
  saveMeta: () => Promise<void>
  submit: () => Promise<void>
}

async function openModal() {
  const wrapper = mountIndex()
  await flushPromises()
  await wrapper.find('[data-testid="create-case-open"]').trigger('click')
  await flushPromises()
  const modal = wrapper.findComponent(CreateCaseModal)
  return { wrapper, modal, vm: modal.vm as unknown as ModalVm }
}

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue({ data: { data: { items: [], total: 0 } } } as never)
  api.candidates.mockResolvedValue({
    data: { data: { items: [POLICY, BASE, REVISED, NOMETA] } }
  } as never)
  api.readiness.mockImplementation(
    async (id: string) =>
      ({
        data: {
          data: {
            document_id: id,
            status: id === 'r1' ? 'blocked' : 'reviewable',
            blocking_reasons: id === 'r1' ? ['revised:no_original_text'] : [],
            warnings: []
          }
        }
      }) as never
  )
})

describe('新建核验建案弹窗', () => {
  it('打开弹窗才加载候选文档；未登记身份文档有显式标记', async () => {
    const wrapper = mountIndex()
    await flushPromises()
    expect(api.candidates).not.toHaveBeenCalled()

    await wrapper.find('[data-testid="create-case-open"]').trigger('click')
    await flushPromises()

    expect(api.candidates).toHaveBeenCalledTimes(1)
    const { vm } = { vm: wrapper.findComponent(CreateCaseModal).vm as unknown as ModalVm }
    expect(vm.candidates).toHaveLength(4)
    expect(vm.optionLabel(NOMETA)).toContain('未登记核验身份')
    expect(vm.optionLabel(BASE)).toContain('合同 V1 (fam)')
  })

  it('选满三份文档后做 readiness 预检，受限文档显示原因码', async () => {
    const { vm } = await openModal()
    vm.sideSelection.policy = 'p1'
    vm.sideSelection.base = 'b1'
    vm.sideSelection.revised = 'r1'
    await flushPromises()

    expect(api.readiness).toHaveBeenCalledTimes(3)
    expect(vm.readiness.base?.status).toBe('reviewable')
    expect(vm.readiness.revised?.status).toBe('blocked')
    expect(vm.readiness.revised?.blocking_reasons).toEqual(['revised:no_original_text'])
  })

  it('提交建案携带三份文档 id 与标题并跳转详情页', async () => {
    api.createCase.mockResolvedValue({ data: { data: { id: 'case-new' } } } as never)
    const { vm } = await openModal()
    vm.form.title = ' 采购合同核验 '
    vm.sideSelection.policy = 'p1'
    vm.sideSelection.base = 'b1'
    vm.sideSelection.revised = 'r1'
    await flushPromises()

    await vm.submit()
    await flushPromises()

    expect(api.createCase).toHaveBeenCalledWith({
      title: '采购合同核验',
      policy_document_id: 'p1',
      base_document_id: 'b1',
      revised_document_id: 'r1',
      checklist_version: 'v1'
    })
    expect(push).toHaveBeenCalledWith('/reviews/case-new')
  })

  it('三份文档重复时不提交建案', async () => {
    const { vm } = await openModal()
    vm.form.title = '重复文档'
    vm.sideSelection.policy = 'p1'
    vm.sideSelection.base = 'p1'
    vm.sideSelection.revised = 'p1'
    await flushPromises()

    await vm.submit()
    await flushPromises()

    expect(api.createCase).not.toHaveBeenCalled()
    expect(push).not.toHaveBeenCalled()
  })

  it('未登记身份文档可原地登记，合同缺版本族/标签/序时拒绝提交', async () => {
    const { vm } = await openModal()
    vm.sideSelection.base = 'n1'
    await flushPromises()

    vm.openMeta('base')
    await flushPromises()
    const modalVm = vm as unknown as {
      metaForm: {
        doc_role: string
        family_key: string
        version_label: string
        version_seq: number | null
      }
    }
    expect(modalVm.metaForm.doc_role).toBe('contract')

    await vm.saveMeta()
    expect(api.putDocumentMeta).not.toHaveBeenCalled()

    modalVm.metaForm.family_key = 'fam'
    modalVm.metaForm.version_label = 'V3'
    modalVm.metaForm.version_seq = 3
    api.putDocumentMeta.mockResolvedValue({
      data: { data: { doc_role: 'contract', family_key: 'fam', version_label: 'V3' } }
    } as never)

    await vm.saveMeta()
    await flushPromises()

    expect(api.putDocumentMeta).toHaveBeenCalledWith('n1', {
      doc_role: 'contract',
      family_key: 'fam',
      version_label: 'V3',
      version_seq: 3
    })
    // 保存后刷新候选，让选中项拿到新身份
    expect(api.candidates).toHaveBeenCalledTimes(2)
  })
})
