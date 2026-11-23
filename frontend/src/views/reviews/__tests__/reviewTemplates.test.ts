/**
 * 核验模板库前端契约（批次五）：列表 / 版本历史 / 只读 diff / 建案选择模板版本。
 *
 * 守护：
 * 1. 列表只请求活跃模板，渲染状态与版本数；空态给创建/初始化出口；
 * 2. 创建弹窗只提交自定义项（内置 4 项由服务端补齐），非法 key/空关键词被前端拦下；
 * 3. 详情页版本历史倒序、选中版本加载内容；归档模板只读（无归档按钮）；
 * 4. diff 弹窗按 基准/对比 版本请求，逐类渲染新增/删除/变更与摘要；
 * 5. 建案弹窗可选模板与版本，提交载荷带 template_id/template_version_no；
 * 6. 不出现紫色系（设计系统禁紫由 check:no-purple 全量扫描，另在本文件兜底）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h } from 'vue'
import {
  NAlert,
  NButton,
  NIcon,
  NInput,
  NMessageProvider,
  NPagination,
  NPopconfirm,
  NSelect,
  NSpin,
  NTag
} from 'naive-ui'

import TemplatesView from '../templateLibrary.vue'
import TemplateDetail from '../templateVersionDetail.vue'
import CreateCaseModal from '../components/CreateCaseModal.vue'
import { reviewsApi } from '@/api/reviews'
import zh from '@/locales/zh'

const routerMocks = vi.hoisted(() => ({
  push: vi.fn()
}))

vi.mock('vue-router', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return {
    ...actual,
    useRouter: () => ({ push: routerMocks.push }),
    useRoute: () => ({ params: { id: 'tpl-1' }, query: {} })
  }
})

vi.mock('@/api/reviews', () => ({
  reviewsApi: {
    candidates: vi.fn(),
    createCase: vi.fn(),
    readiness: vi.fn(),
    putDocumentMeta: vi.fn(),
    templates: {
      list: vi.fn(),
      create: vi.fn(),
      bootstrap: vi.fn(),
      get: vi.fn(),
      update: vi.fn(),
      archive: vi.fn(),
      versions: vi.fn(),
      createVersion: vi.fn(),
      getVersion: vi.fn(),
      diff: vi.fn()
    }
  }
}))

const api = vi.mocked(reviewsApi, true)

const TEMPLATE = {
  id: 'tpl-1',
  organization_id: 1,
  name: '采购模板',
  description: '采购合同核验',
  status: 'active',
  latest_version: 2,
  created_by: 1,
  updated_by: 1,
  lock_version: 2,
  created_at: '2026-11-20T00:00:00Z',
  updated_at: '2026-11-22T00:00:00Z'
}

const BUILTIN = [
  { key: 'payment_term', keywords: ['付款'], label: '', configurable: false },
  { key: 'start_condition', keywords: ['起算'], label: '', configurable: false },
  { key: 'acceptance_invoice', keywords: ['验收'], label: '', configurable: false },
  { key: 'exception', keywords: ['例外'], label: '', configurable: false }
]

const V2 = {
  id: 'ver-2',
  template_id: 'tpl-1',
  version_no: 2,
  version_label: 'v2',
  item_count: 5,
  payload_sha256: 'b'.repeat(64),
  source: 'manual',
  change_note: '追加保密项',
  created_by: 1,
  created_at: '2026-11-22T00:00:00Z',
  items: [...BUILTIN, { key: 'confidentiality', keywords: ['保密'], label: '保密义务', configurable: true }],
  intact: true
}

const V1 = {
  ...V2,
  id: 'ver-1',
  version_no: 1,
  version_label: 'v1',
  payload_sha256: 'a'.repeat(64),
  source: 'bootstrap',
  change_note: null,
  created_at: '2026-11-20T00:00:00Z',
  items: BUILTIN,
  item_count: 4
}

const SUMMARY = (row: { version_no: number; version_label: string; item_count: number; payload_sha256: string; source: string; id: string; change_note: string | null; created_at: string }) => ({
  id: row.id,
  template_id: 'tpl-1',
  version_no: row.version_no,
  version_label: row.version_label,
  item_count: row.item_count,
  payload_sha256: row.payload_sha256,
  source: row.source,
  change_note: row.change_note,
  created_by: 1,
  created_at: row.created_at
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const NButtonStub = defineComponent({
  name: 'NButton',
  setup(_, { attrs, slots, emit }) {
    return () => h('button', { ...attrs, type: 'button', onClick: () => emit('click') }, slots.default?.())
  }
})

const PAGE_GLOBAL = {
  plugins: [i18n],
  components: { NAlert, NButton, NIcon, NInput, NMessageProvider, NPagination, NPopconfirm, NSelect, NSpin, NTag },
  stubs: { PageBreadcrumb: true, teleport: true, NButton: NButtonStub }
}

/** useMessage 需要外层 provider：用宿主组件包住两个页面（与 reviewCreate 同法）。 */
function mountWithProvider(component: unknown) {
  return mount(
    {
      components: { Page: component as never, NMessageProvider },
      template: '<n-message-provider><Page /></n-message-provider>'
    },
    { global: PAGE_GLOBAL }
  )
}

function mountTemplates() {
  return mountWithProvider(TemplatesView)
}

function mountDetail() {
  return mountWithProvider(TemplateDetail)
}

beforeEach(() => {
  vi.clearAllMocks()
  api.templates.list.mockResolvedValue({
    data: { success: true, data: { items: [TEMPLATE], total: 1 } }
  } as never)
  api.templates.get.mockResolvedValue({ data: { success: true, data: { ...TEMPLATE, version: V2 } } } as never)
  api.templates.versions.mockResolvedValue({
    data: {
      success: true,
      data: { items: [SUMMARY(V2), SUMMARY(V1)], total: 2, page: 1, page_size: 100, latest_version: 2 }
    }
  } as never)
  api.templates.getVersion.mockImplementation((async (_id: string, versionNo: number) => ({
    data: { success: true, data: versionNo === 2 ? V2 : V1 }
  })) as never)
  api.templates.diff.mockResolvedValue({
    data: {
      success: true,
      data: {
        template_id: 'tpl-1',
        base: { id: 'ver-1', version_no: 1, version_label: 'v1', payload_sha256: 'a'.repeat(64), item_count: 4 },
        target: { id: 'ver-2', version_no: 2, version_label: 'v2', payload_sha256: 'b'.repeat(64), item_count: 5 },
        identical: false,
        added: [{ key: 'confidentiality', keywords: ['保密'], label: '保密义务', configurable: true }],
        removed: [],
        changed: [
          {
            key: 'termination',
            fields: {
              keywords: { base: ['解除'], target: ['解除', '终止'], added: ['终止'], removed: [] },
              label: { base: '解除权', target: '解除权（含终止）' }
            }
          }
        ],
        unchanged_keys: [],
        summary: { added: 1, removed: 0, changed: 1, unchanged: 0 }
      }
    }
  } as never)
})

describe('模板库列表页', () => {
  it('只请求活跃模板并渲染状态/版本数；空态给创建与初始化出口', async () => {
    const wrapper = mountTemplates()
    await flushPromises()
    expect(api.templates.list).toHaveBeenCalledWith({ status: 'active', page: 1, page_size: 20 })
    expect(wrapper.text()).toContain('采购模板')
    expect(wrapper.text()).toContain('2 个版本')
    expect(wrapper.find('[data-testid="template-row-tpl-1"]').exists()).toBe(true)

    api.templates.list.mockResolvedValue({ data: { success: true, data: { items: [], total: 0 } } } as never)
    const empty = mountTemplates()
    await flushPromises()
    expect(empty.find('[data-testid="templates-empty-state"]').exists()).toBe(true)
    expect(empty.find('[data-testid="template-create-empty"]').exists()).toBe(true)
  })

  it('创建只提交自定义项（内置 4 项不随载荷提交），成功后跳详情', async () => {
    api.templates.create.mockResolvedValue({ data: { success: true, data: { id: 'tpl-new' } } } as never)
    const wrapper = mountTemplates()
    await flushPromises()
    await wrapper.find('[data-testid="template-create-open"]').trigger('click')
    await flushPromises()

    const vm = wrapper.findComponent(TemplatesView).vm as unknown as {
      form: { name: string; description: string; items: Array<{ key: string; label: string; keywordsText: string }> }
      addItem: () => void
      submitCreate: () => Promise<void>
    }
    vm.form.name = '新模板'
    vm.form.description = '说明'
    vm.addItem()
    vm.form.items[0].key = 'confidentiality'
    vm.form.items[0].label = '保密义务'
    vm.form.items[0].keywordsText = '保密，泄露, 保密'
    await vm.submitCreate()

    expect(api.templates.create).toHaveBeenCalledWith({
      name: '新模板',
      description: '说明',
      items: [{ key: 'confidentiality', keywords: ['保密', '泄露'], label: '保密义务' }]
    })
    expect(routerMocks.push).toHaveBeenCalledWith('/reviews/templates/tpl-new')
  })

  it('非法 key 与空关键词被前端拦下（不发请求）', async () => {
    const wrapper = mountTemplates()
    await flushPromises()
    await wrapper.find('[data-testid="template-create-open"]').trigger('click')
    await flushPromises()
    const vm = wrapper.findComponent(TemplatesView).vm as unknown as {
      form: { name: string; items: Array<{ key: string; label: string; keywordsText: string }> }
      addItem: () => void
      submitCreate: () => Promise<void>
    }
    vm.form.name = 'x'
    vm.addItem()
    vm.form.items[0].key = 'BadKey'
    vm.form.items[0].keywordsText = '付款'
    await vm.submitCreate()
    expect(api.templates.create).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="template-create-error"]').text()).toContain('key 规则')

    vm.form.items[0].key = 'confidentiality'
    vm.form.items[0].keywordsText = '  ,  '
    await vm.submitCreate()
    expect(api.templates.create).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="template-create-error"]').text()).toContain('关键词')
  })
})

describe('模板详情：版本历史 + 只读 diff', () => {
  it('版本历史倒序展示，默认选中最新版并渲染内置/自定义标记', async () => {
    const wrapper = mountDetail()
    await flushPromises()
    expect(api.templates.versions).toHaveBeenCalledWith('tpl-1', { page: 1, page_size: 100 })
    expect(api.templates.getVersion).toHaveBeenCalledWith('tpl-1', 2)
    const list = wrapper.find('[data-testid="version-list"]')
    expect(list.text()).toContain('v2')
    expect(list.text()).toContain('v1')
    expect(wrapper.find('[data-testid="version-item-payment_term"]').text()).toContain('内置')
    expect(wrapper.find('[data-testid="version-item-confidentiality"]').text()).toContain('自定义')
    // bootstrap 来源的版本有显式标记
    expect(list.text()).toContain('初始化')
  })

  it('选择旧版本会加载该版本内容', async () => {
    const wrapper = mountDetail()
    await flushPromises()
    await wrapper.find('[data-testid="version-select-1"]').trigger('click')
    await flushPromises()
    expect(api.templates.getVersion).toHaveBeenCalledWith('tpl-1', 1)
    expect(wrapper.text()).toContain('v1')
  })

  it('diff 弹窗请求基准/对比版本并逐类渲染（摘要、新增、变更、一致性）', async () => {
    const wrapper = mountDetail()
    await flushPromises()
    await wrapper.find('[data-testid="diff-open"]').trigger('click')
    await flushPromises()
    expect(api.templates.diff).toHaveBeenCalledWith('tpl-1', 1, 2)
    expect(wrapper.find('[data-testid="diff-summary"]').text()).toContain('新增 1')
    expect(wrapper.find('[data-testid="diff-added"]').text()).toContain('confidentiality')
    const changed = wrapper.find('[data-testid="diff-changed-termination"]')
    expect(changed.text()).toContain('终止')
    expect(changed.text()).toContain('解除权（含终止）')
  })

  it('归档模板只读：显示归档横幅且不渲染归档按钮', async () => {
    api.templates.get.mockResolvedValue({
      data: { success: true, data: { ...TEMPLATE, status: 'archived', archived_at: '2026-11-23T00:00:00Z', version: V2 } }
    } as never)
    const wrapper = mountDetail()
    await flushPromises()
    expect(wrapper.find('[data-testid="template-archived-banner"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="template-archive"]').exists()).toBe(false)
  })
})

describe('设计系统兜底', () => {
  it('新页面源码不含紫色系类名/色值', async () => {
    const { readFileSync } = await import('node:fs')
    const { resolve } = await import('node:path')
    for (const file of ['templateLibrary.vue', 'templateVersionDetail.vue', 'components/CreateCaseModal.vue']) {
      const source = readFileSync(resolve(process.cwd(), 'src/views/reviews', file), 'utf-8')
      expect(source.toLowerCase(), `${file} 出现紫色系关键字`).not.toMatch(
        /purple|violet|indigo|fuchsia|pink/
      )
    }
  })
})

describe('建案：模板版本选择', () => {
  const POLICY = { document_id: 'p1', filename: '制度.pdf', status: 'indexed', meta: { doc_role: 'policy' } }
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

  async function openModal() {
    api.candidates.mockResolvedValue({
      data: { data: { items: [POLICY, BASE, REVISED], total: 3 } }
    } as never)
    api.readiness.mockResolvedValue({
      data: { data: { status: 'reviewable', blocking_reasons: [], warnings: [] } }
    } as never)
    const wrapper = mountWithProvider(
      defineComponent({
        components: { CreateCaseModal, NButton },
        data: () => ({ show: false }),
        template: '<div><n-button data-testid="open" @click="show = true" /><CreateCaseModal :show="show" /></div>'
      })
    )
    await flushPromises()
    // 与产品一致：打开弹窗才加载候选/模板（关闭态不发请求）。
    expect(api.candidates).not.toHaveBeenCalled()
    await wrapper.find('[data-testid="open"]').trigger('click')
    await flushPromises()
    const modal = wrapper.findComponent(CreateCaseModal)
    return { wrapper, vm: modal.vm as unknown as Record<string, any> }
  }

  it('打开弹窗加载活跃模板；选中模板后加载其版本', async () => {
    const { vm } = await openModal()
    expect(api.templates.list).toHaveBeenCalledWith({ status: 'active', page: 1, page_size: 100 })
    expect(vm.templates).toHaveLength(1)
    expect(vm.templateOptions[0].label).toContain('采购模板')

    await vm.onTemplateChange('tpl-1')
    await flushPromises()
    expect(api.templates.versions).toHaveBeenCalledWith('tpl-1', { page: 1, page_size: 100 })
    expect(vm.templateVersionOptions.map((o: { value: number }) => o.value)).toEqual([2, 1])
  })

  it('提交载荷带 template_id 与选定版本；未选版本时不带 version_no', async () => {
    api.templates.list.mockResolvedValue({
      data: { success: true, data: { items: [TEMPLATE], total: 1 } }
    } as never)
    api.templates.versions.mockResolvedValue({
      data: { success: true, data: { items: [SUMMARY(V2), SUMMARY(V1)], total: 2, page: 1, page_size: 100, latest_version: 2 } }
    } as never)
    api.createCase.mockResolvedValue({ data: { data: { id: 'case-tpl' } } } as never)
    const { vm } = await openModal()
    vm.form.title = '模板建案'
    vm.sideSelection.policy = 'p1'
    vm.sideSelection.base = 'b1'
    vm.sideSelection.revised = 'r1'
    await flushPromises()
    vm.templateId = 'tpl-1'
    await vm.onTemplateChange('tpl-1')
    vm.templateVersionNo = 1
    await vm.submit()
    await flushPromises()
    expect(api.createCase).toHaveBeenCalledWith({
      title: '模板建案',
      policy_document_id: 'p1',
      base_document_id: 'b1',
      revised_document_id: 'r1',
      checklist_version: 'v1',
      template_id: 'tpl-1',
      template_version_no: 1
    })

    vi.clearAllMocks()
    api.templates.list.mockResolvedValue({
      data: { success: true, data: { items: [TEMPLATE], total: 1 } }
    } as never)
    api.createCase.mockResolvedValue({ data: { data: { id: 'case-tpl2' } } } as never)
    const second = await openModal()
    second.vm.form.title = '最新版本'
    second.vm.sideSelection.policy = 'p1'
    second.vm.sideSelection.base = 'b1'
    second.vm.sideSelection.revised = 'r1'
    await flushPromises()
    second.vm.templateId = 'tpl-1'
    await second.vm.onTemplateChange('tpl-1')
    await second.vm.submit()
    await flushPromises()
    const payload = vi.mocked(api.createCase).mock.calls.at(-1)?.[0] as unknown as Record<string, unknown>
    expect(payload.template_id).toBe('tpl-1')
    expect('template_version_no' in payload).toBe(false)
  })
})
