/**
 * 新建核验（建案弹窗）契约测试。
 *
 * 守护：
 * 1. 打开弹窗才加载候选文档；未登记身份的文档有显式标记与登记入口；
 * 2. 选满三份文档后才做 readiness 预检，受限文档显示原因码；旧响应不落地；
 * 3. 建案载荷 = 三份文档 id + 标题 + checklist v1，成功后跳详情；
 * 4. 三份文档重复时不提交；合同身份缺版本族/标签/序时不提交；
 * 5. 身份可原地登记/编辑；角色按侧锁定；登记后不匹配该侧的选择被清空；
 * 6. 跨文档约束（版本族/版本序）在建案前预告（文案不含机器码）；
 * 7. 候选加载失败可重试；超过 200 篇时给出截断提示。
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
        components: { NButton, NDataTable, NEmpty, NPagination, NSelect, NSpin, NTag },
        // 弹窗内容 teleport 到 body；测试里就地渲染以便 wrapper.text() 断言。
        stubs: { teleport: true }
      }
    }
  )
}

type CandidateLike = { document_id: string; filename: string; status: string; meta: unknown }
type OptionLike = { value: string; label: string }

type ModalVm = {
  form: { title: string }
  sideSelection: Record<'policy' | 'base' | 'revised', string | null>
  candidates: CandidateLike[]
  candidateTotal: number
  candidatesError: boolean
  readiness: Record<string, { status: string; blocking_reasons: string[] } | undefined>
  crossDocWarning: string
  roleOptions: Array<{ value: string; label: string }>
  optionLabel: (candidate: unknown) => string
  optionsFor: (side: 'policy' | 'base' | 'revised') => OptionLike[]
  openMeta: (side: 'policy' | 'base' | 'revised') => void
  saveMeta: () => Promise<void>
  submit: () => Promise<void>
  loadCandidates: () => Promise<void>
}

async function openModal() {
  const wrapper = mountIndex()
  await flushPromises()
  await wrapper.find('[data-testid="create-case-open"]').trigger('click')
  await flushPromises()
  const modal = wrapper.findComponent(CreateCaseModal)
  return { wrapper, modal, vm: modal.vm as unknown as ModalVm }
}

function candidatesResponse(items: CandidateLike[], total?: number) {
  return { data: { data: { items, total: total ?? items.length } } } as never
}

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue({ data: { data: { items: [], total: 0 } } } as never)
  api.candidates.mockResolvedValue(candidatesResponse([POLICY, BASE, REVISED, NOMETA]))
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
    const vm = wrapper.findComponent(CreateCaseModal).vm as unknown as ModalVm
    expect(vm.candidates).toHaveLength(4)
    expect(vm.optionLabel(NOMETA)).toContain('未登记核验身份')
    expect(vm.optionLabel(BASE)).toContain('合同 V1 (fam)')
  })

  it('选满三份文档后才做 readiness 预检，受限文档显示原因码', async () => {
    const { wrapper, vm } = await openModal()
    vm.sideSelection.policy = 'p1'
    await flushPromises()
    expect(api.readiness).not.toHaveBeenCalled()

    vm.sideSelection.base = 'b1'
    vm.sideSelection.revised = 'r1'
    await flushPromises()

    expect(api.readiness).toHaveBeenCalledTimes(3)
    expect(vm.readiness.base?.status).toBe('reviewable')
    expect(vm.readiness.revised?.status).toBe('blocked')
    expect(vm.readiness.revised?.blocking_reasons).toEqual(['revised:no_original_text'])
    // 预检原因码本地化上屏（详情页口径一致）
    expect(wrapper.text()).toContain('缺少 parser 原文')
  })

  it('readiness 竞态：旧一批响应不覆盖新选择的结果', async () => {
    const deferred: Array<{ id: string; resolve: (v: unknown) => void }> = []
    api.readiness.mockImplementation(
      (id: string) =>
        new Promise((resolve) => {
          deferred.push({ id, resolve })
        }) as never
    )
    const { vm } = await openModal()
    vm.sideSelection.policy = 'p1'
    vm.sideSelection.base = 'b1'
    vm.sideSelection.revised = 'r1'
    await flushPromises()
    expect(deferred.map((d) => d.id).sort()).toEqual(['b1', 'p1', 'r1'])

    vm.sideSelection.revised = 'r2'
    await flushPromises()
    const second = deferred.slice(3)
    expect(second.map((d) => d.id).sort()).toEqual(['b1', 'p1', 'r2'])

    const respond = (batch: typeof deferred, status: string) => {
      for (const entry of batch) {
        entry.resolve({
          data: { data: { document_id: entry.id, status, blocking_reasons: [], warnings: [] } }
        })
      }
    }
    // 新一批先返回；旧一批随后返回也不得覆盖。
    respond(second, 'reviewable')
    await flushPromises()
    respond(deferred.slice(0, 3), 'blocked')
    await flushPromises()
    expect(vm.readiness.base?.status).toBe('reviewable')
    expect(vm.readiness.revised?.status).toBe('reviewable')
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
    expect(api.candidates).toHaveBeenCalledTimes(2)
  })

  it('首次登记角色按侧锁定；编辑已有身份放开三角色（修复路径）', async () => {
    const { vm } = await openModal()
    vm.sideSelection.policy = 'n1'
    vm.sideSelection.base = 'n1'
    await flushPromises()

    // 未登记：制度侧只能 policy/template，合同侧只能 contract
    vm.openMeta('policy')
    await flushPromises()
    expect(vm.roleOptions.map((option) => option.value)).toEqual(['policy', 'template'])
    vm.openMeta('base')
    await flushPromises()
    expect(vm.roleOptions.map((option) => option.value)).toEqual(['contract'])

    // 已登记：编辑时放开全部角色，允许纠正错误登记
    vm.sideSelection.base = 'b1'
    await flushPromises()
    vm.openMeta('base')
    await flushPromises()
    expect(vm.roleOptions.map((option) => option.value)).toEqual([
      'policy',
      'template',
      'contract'
    ])
  })

  it('已登记身份的文档可编辑身份，弹窗预填现有值', async () => {
    const { wrapper, vm } = await openModal()
    vm.sideSelection.base = 'b1'
    await flushPromises()
    // 已登记文档也要有编辑入口（防死胡同回归）
    expect(wrapper.text()).toContain('编辑身份')

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
    expect(modalVm.metaForm.family_key).toBe('fam')
    expect(modalVm.metaForm.version_label).toBe('V1')
    expect(modalVm.metaForm.version_seq).toBe(1)

    modalVm.metaForm.version_label = 'V1b'
    api.putDocumentMeta.mockResolvedValue({ data: { data: {} } } as never)
    await vm.saveMeta()

    expect(api.putDocumentMeta).toHaveBeenCalledWith('b1', {
      doc_role: 'contract',
      family_key: 'fam',
      version_label: 'V1b',
      version_seq: 1
    })
  })

  it('身份变更后不再匹配该侧角色的已选项被清空', async () => {
    const { vm } = await openModal()
    vm.sideSelection.policy = 'n1'
    await flushPromises()

    api.candidates.mockResolvedValue(
      candidatesResponse([
        POLICY,
        BASE,
        REVISED,
        {
          ...NOMETA,
          meta: { doc_role: 'contract', family_key: 'fam2', version_label: 'V9', version_seq: 9 }
        }
      ])
    )
    await vm.loadCandidates()
    await flushPromises()

    expect(vm.sideSelection.policy).toBeNull()
  })

  it('跨文档约束在建案前预告：版本族不一致与版本序倒挂', async () => {
    const { wrapper, vm } = await openModal()
    vm.sideSelection.base = 'b1'
    vm.sideSelection.revised = 'r1'
    await flushPromises()
    expect(vm.crossDocWarning).toBe('')

    api.candidates.mockResolvedValue(
      candidatesResponse([
        POLICY,
        BASE,
        { ...REVISED, meta: { ...REVISED.meta, family_key: 'famB' } },
        NOMETA
      ])
    )
    await vm.loadCandidates()
    await flushPromises()
    expect(vm.crossDocWarning).toContain('版本族不一致')
    expect(wrapper.text()).toContain('版本族不一致')
    expect(wrapper.text()).not.toContain('family_mismatch')

    api.candidates.mockResolvedValue(
      candidatesResponse([
        POLICY,
        BASE,
        { ...REVISED, meta: { ...REVISED.meta, version_seq: 1 } },
        NOMETA
      ])
    )
    await vm.loadCandidates()
    await flushPromises()
    expect(vm.crossDocWarning).toContain('版本序不小于')
  })

  it('其他侧已选的文档不再出现在本侧选项里', async () => {
    const { vm } = await openModal()
    // 用未登记文档（能通过角色过滤）才能真正锁住跨侧剔除
    vm.sideSelection.policy = 'n1'
    await flushPromises()

    const baseOptions = vm.optionsFor('base').map((option) => option.value)
    expect(baseOptions).not.toContain('n1')
    expect(baseOptions).toContain('b1')
  })

  it('候选加载失败给出内联错误并可重试', async () => {
    api.candidates.mockRejectedValueOnce(new Error('boom'))
    const { vm } = await openModal()
    expect(vm.candidatesError).toBe(true)
    expect(vm.candidates).toHaveLength(0)

    await vm.loadCandidates()
    await flushPromises()
    expect(vm.candidatesError).toBe(false)
    expect(vm.candidates).toHaveLength(4)
  })

  it('候选超过加载上限时给出截断提示', async () => {
    api.candidates.mockResolvedValue(candidatesResponse([POLICY, BASE], 321))
    const { wrapper, vm } = await openModal()
    expect(vm.candidateTotal).toBe(321)
    expect(vm.candidates).toHaveLength(2)
    expect(wrapper.text()).toContain('共 321 篇')
  })
})

describe('身份纠错闭环（修复路径）', () => {
  it('选错侧首次登记后可从同一侧改回正确角色', async () => {
    const { vm } = await openModal()
    vm.sideSelection.policy = 'n1'
    await flushPromises()

    // 第一次登记：把合同误选进制度侧，登记为 policy
    const policyN1 = { ...NOMETA, meta: { doc_role: 'policy' } }
    api.candidates.mockResolvedValue(candidatesResponse([POLICY, BASE, REVISED, policyN1]))
    api.putDocumentMeta.mockResolvedValue({ data: { data: {} } } as never)
    vm.openMeta('policy')
    await flushPromises()
    await vm.saveMeta()
    await flushPromises()
    expect(api.putDocumentMeta).toHaveBeenLastCalledWith('n1', { doc_role: 'policy' })
    expect(vm.sideSelection.policy).toBe('n1')

    // 再编辑：编辑已有身份放开三角色，改回 contract 并补齐版本信息
    const contractN1 = {
      ...NOMETA,
      meta: { doc_role: 'contract', family_key: 'fam', version_label: 'V1', version_seq: 1 }
    }
    api.candidates.mockResolvedValue(candidatesResponse([POLICY, BASE, REVISED, contractN1]))
    vm.openMeta('policy')
    await flushPromises()
    expect(vm.roleOptions.map((option) => option.value)).toEqual([
      'policy',
      'template',
      'contract'
    ])
    const modalVm = vm as unknown as {
      metaForm: {
        doc_role: string
        family_key: string
        version_label: string
        version_seq: number | null
      }
    }
    modalVm.metaForm.doc_role = 'contract'
    modalVm.metaForm.family_key = 'fam'
    modalVm.metaForm.version_label = 'V1'
    modalVm.metaForm.version_seq = 1
    await vm.saveMeta()
    await flushPromises()

    expect(api.putDocumentMeta).toHaveBeenLastCalledWith('n1', {
      doc_role: 'contract',
      family_key: 'fam',
      version_label: 'V1',
      version_seq: 1
    })
    // 刷新后制度侧选择被清空，文档转入合同侧候选池
    expect(vm.sideSelection.policy).toBeNull()
    expect(vm.optionsFor('base').map((option) => option.value)).toContain('n1')
  })
})
