/**
 * 知识库页数据契约判别用例（UI 验收 round2 · 复用挂载基座）。
 *
 * 本文件随各子项提交逐条生长：
 * - A2 批删组织组包（本提交）：handleBatchDelete 全链路 → 请求带 organization_id
 * - ② file_type 枚举（docDisplay.test.ts 另覆盖纯函数）
 * - ③ 状态机标签接线（详情桩按 getStatusLabel 出屏）
 * - ④ 幽灵重试向量化控件清除
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider, NDialogProvider, NCheckbox, NTooltip } from 'naive-ui'

import KnowledgeView from '../index.vue'
import { getKnowledgeBases, batchDeleteKnowledgeBases, getDocumentDetail, getDocumentContent } from '@/api/knowledge'
import { useUserStore } from '@/stores/user'
import type { UserInfo } from '@/types/user'
import zh from '@/locales/zh'

vi.mock('@/api/knowledge', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/knowledge')>()
  return {
    ...actual,
    getKnowledgeBases: vi.fn(),
    getDocumentDetail: vi.fn(),
    getDocumentContent: vi.fn(),
    batchDeleteKnowledgeBases: vi.fn(),
    deleteKnowledgeBase: vi.fn(),
    rebuildKnowledgeBase: vi.fn(),
    uploadKnowledgeBase: vi.fn(),
  }
})
vi.mock('@/api/rag', () => ({
  getRagMetrics: vi.fn().mockResolvedValue({ data: { data: null } }),
}))

const mockedGetKnowledgeBases = vi.mocked(getKnowledgeBases)
const mockedBatchDelete = vi.mocked(batchDeleteKnowledgeBases)
const mockedGetDocumentDetail = vi.mocked(getDocumentDetail)
const mockedGetDocumentContent = vi.mocked(getDocumentContent)

// ── Fixtures（响应形状逐字对齐 knowledge.py:158-167 双层 data + :144-156 item）──
const REAL_DOC = {
  id: 'doc-1',
  title: 'architecture.md',
  file_name: 'architecture.md',
  file_type: 'word',
  file_size: 1578,
  tags: ['技术文档'],
  description: null,
  parse_error: null,
  created_at: '2026-10-08T05:29:00',
  updated_at: '2026-10-08T05:30:00',
  status: 'parsing',
}

const listResponse = (docs: unknown[], total?: number) => ({
  data: {
    success: true,
    message: '获取成功',
    data: { data: docs, total: total ?? docs.length, page: 1, page_size: 12 },
  },
})

const SlotStub = (name: string) =>
  defineComponent({ name, setup(_, { slots }) { return () => h('div', slots.default?.()) } })

// 详情弹窗桩：把视图传入的 getStatusLabel/getStatusType 对当前文档的求值结果出屏，
// 用于断言状态标签走的是视图真实接线
const DetailStub = defineComponent({
  name: 'KnowledgeDetailModals',
  props: {
    showDetail: { type: Boolean, default: false },
    currentDoc: { type: Object, default: null },
    getStatusLabel: { type: Function, default: () => '' },
    getStatusType: { type: Function, default: () => 'default' },
  },
  setup(props) {
    return () =>
      h(
        'div',
        { class: 'detail-stub' },
        props.currentDoc
          ? `${(props.getStatusLabel as (d: unknown) => string)(props.currentDoc)}|${(props.getStatusType as (d: unknown) => string)(props.currentDoc)}|${(props.currentDoc as { preview_provenance?: string }).preview_provenance ?? ''}|${(props.currentDoc as { preview_truncated?: boolean }).preview_truncated ? 'truncated' : 'full'}`
          : ''
      )
  },
})

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NDialogProvider, null, { default: () => h(NMessageProvider, null, { default: () => h(KnowledgeView) }) }),
})

function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.userInfo = {
    id: 1, username: 'tester', email: 't@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
  } as UserInfo

  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n],
      // n-checkbox / n-tooltip 生产由 app.use(naive) 全局注册；测试里注册真组件
      // （n-tooltip 的 #trigger 槽装着卡片「查看」按钮，缺失则点击链断裂）
      components: { NCheckbox, NTooltip },
      stubs: {
        KnowledgeUploadPanel: true,
        KnowledgeDetailModals: DetailStub,
        Pagination: true,
        Select: true,
        Input: true,
        RadioGroup: true,
        RadioButton: true,
        Grid: SlotStub('Grid'),
        Gi: SlotStub('Gi'),
        Spin: SlotStub('Spin'),
        Tag: SlotStub('Tag'),
      },
    },
  })
  return wrapper
}

function findButtonByText(wrapper: ReturnType<typeof mountView>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

/** 实体对话框被 NDialogProvider teleport 到 document.body，从 body 查询「确认」 */
function findDialogConfirm(): HTMLButtonElement | undefined {
  return Array.from(document.body.querySelectorAll('button')).find(
    (b) => b.textContent?.trim() === '确认'
  )
}

beforeEach(() => {
  mockedGetKnowledgeBases.mockReset()
  mockedGetDocumentDetail.mockReset()
  mockedGetDocumentContent.mockReset()
  mockedBatchDelete.mockReset()
})

afterEach(() => {
  // 清掉 teleport 到 body 的对话框容器，避免跨用例残留
  document.body.innerHTML = ''
})

describe('KnowledgeView · 批删组织组包（A2）', () => {  it('全链路：勾选 → 批量删除 → 确认 → 请求带 organization_id=3（userStore.currentOrgId）', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
    mockedBatchDelete.mockResolvedValue(undefined as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 勾选文档（grid 卡片上的 n-checkbox）
    const checkbox = wrapper.find('.n-checkbox')
    expect(checkbox.exists()).toBe(true)
    await checkbox.trigger('click')
    await nextTick()

    const batchBtn = findButtonByText(wrapper, '批量删除')
    expect(batchBtn, '勾选后应出现批量删除按钮').toBeTruthy()
    await batchBtn!.trigger('click')
    await flushPromises()
    await nextTick()

    // 实体对话框（NDialogProvider teleport 到 document.body）
    const confirmBtn = findDialogConfirm()
    expect(confirmBtn, '确认对话框应打开').toBeTruthy()
    confirmBtn!.click()
    await flushPromises()
    await nextTick()

    expect(mockedBatchDelete).toHaveBeenCalledTimes(1)
    // E14/D1：页面唯一 toast → 请求静默（X-Silent-Error），第三参为静默选项
    expect(mockedBatchDelete).toHaveBeenCalledWith(['doc-1'], 3, { silent: true })
  })

  it('无当前组织（超管场景）：请求仍发出、organization_id 传 null 由 api 层省略', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
    mockedBatchDelete.mockResolvedValue(undefined as never)

    const wrapper = mountView()
    const user = useUserStore()
    user.userInfo = { ...(user.userInfo as UserInfo), organization_id: null } as unknown as UserInfo

    await flushPromises()
    await wrapper.find('.n-checkbox').trigger('click')
    await nextTick()
    const batchBtn = findButtonByText(wrapper, '批量删除')
    await batchBtn!.trigger('click')
    await flushPromises()
    await nextTick()
    const confirmBtn = findDialogConfirm()
    expect(confirmBtn).toBeTruthy()
    confirmBtn!.click()
    await flushPromises()

    expect(mockedBatchDelete).toHaveBeenCalledWith(['doc-1'], null, { silent: true })
  })
})

describe('KnowledgeView · file_type 图标配色（B15）', () => {
  it('file_type=word（真实枚举）→ 卡片图标配色为 word 蓝 rgb(77,171,247)', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const iconStyles = wrapper.findAll('.n-icon').map((n) => n.attributes('style') || '')
    // 原配色表无 'word' 键 → 恒灰色兜底；修复后命中 #4dabf7 = rgb(77,171,247)
    expect(iconStyles.some((s) => /77,\s*171,\s*247/.test(s))).toBe(true)
  })
})

describe('KnowledgeView · 状态标签接线（B16）', () => {
  it('status=parsing 文档打开详情 → 「解析中」（旧实现落 else 谎报「已完成」）', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([{ ...REAL_DOC, status: 'parsing' }]) as never)
    mockedGetDocumentDetail.mockResolvedValue({
      data: { data: { id: 'doc-1', title: 'architecture.md', filename: 'architecture.md', file_size: 1578, file_type: 'word', status: 'parsing' } },
    } as never)
    mockedGetDocumentContent.mockResolvedValue({
      data: { data: { content: '预览内容', summary: '', suggested_tags: [] } },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const card = wrapper.findAll('.group').find((g) => g.text().includes('architecture.md'))
    expect(card, '文档卡应在屏').toBeTruthy()
    await card!.findAll('button')[0].trigger('click')
    await flushPromises()
    await nextTick()

    const stub = wrapper.find('.detail-stub')
    expect(stub.exists()).toBe(true)
    expect(stub.text()).toContain('解析中')
    expect(stub.text()).not.toContain('已索引')
  })

  it('status=pending 文档打开详情 → 「等待处理」（不谎报终态）', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([{ ...REAL_DOC, status: 'pending' }]) as never)
    mockedGetDocumentDetail.mockResolvedValue({
      data: { data: { id: 'doc-1', title: 'architecture.md', filename: 'architecture.md', file_size: 1578, file_type: 'word', status: 'pending' } },
    } as never)
    mockedGetDocumentContent.mockResolvedValue({
      data: { data: { content: '预览内容', summary: '', suggested_tags: [] } },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const card = wrapper.findAll('.group').find((g) => g.text().includes('architecture.md'))
    await card!.findAll('button')[0].trigger('click')
    await flushPromises()
    await nextTick()

    const stub = wrapper.find('.detail-stub')
    expect(stub.text()).toContain('等待处理')
    expect(stub.text()).not.toContain('已索引')
  })

  it('详情接口词表（status=indexed）与列表（completed）统一渲染「已索引」（K1：弹窗曾显示「未知」）', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([{ ...REAL_DOC, status: 'completed' }]) as never)
    // 详情端点真实返回词表：documents.py:296-303 原样吐 'indexed'
    mockedGetDocumentDetail.mockResolvedValue({
      data: { data: { id: 'doc-1', title: 'architecture.md', filename: 'architecture.md', file_size: 1578, file_type: 'word', status: 'indexed' } },
    } as never)
    mockedGetDocumentContent.mockResolvedValue({
      data: { data: { content: '预览内容', summary: '', suggested_tags: [] } },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    const card = wrapper.findAll('.group').find((g) => g.text().includes('architecture.md'))
    await card!.findAll('button')[0].trigger('click')
    await flushPromises()
    await nextTick()

    const stub = wrapper.find('.detail-stub')
    expect(stub.exists()).toBe(true)
    // 合并详情后 currentDoc.status='indexed' → 必须与列表 completed 一样渲染「已索引」
    expect(stub.text()).toContain('已索引')
    expect(stub.text()).not.toContain('未知')
    expect(stub.text()).not.toContain('indexed')
    expect(stub.text()).not.toContain('completed')
  })
})

describe('KnowledgeView · 统计卡口径（词表收敛 7.2⑤）', () => {
  it('列表吐收敛后新词表 indexed → 统计卡「已索引」计数不为 0', async () => {
    // 后端本波起列表与详情同吐 'indexed'（DB 枚举原词）。
    // 统计卡若只认旧词 'completed'，此处 completed 会归零（红）。
    mockedGetKnowledgeBases.mockResolvedValue(
      listResponse([
        { ...REAL_DOC, id: 'doc-1', status: 'indexed' },
        { ...REAL_DOC, id: 'doc-2', status: 'parsing' },
        { ...REAL_DOC, id: 'doc-3', status: 'failed' },
      ]) as never
    )

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 统计卡四个数字，DOM 顺序：总数 / 已索引 / 处理中 / 失败
    // （视图数字元素为 p.text-2xl.font-bold，页面大标题为 h1、RAG 指标区为 text-xl，不混入）
    const nums = wrapper.findAll('p.text-2xl.font-bold').map((n) => n.text()).slice(0, 4)
    expect(nums).toEqual(['3', '1', '1', '1'])
  })
})

describe('KnowledgeView · 幽灵降级控件已清除（B17）', () => {
  it('completed + parse_error 含「降级」也绝无「重试向量化」/「重试全部降级文档」入口', async () => {
    // 旧 isFallbackIndexed 的唯一触发面：status==='completed' 且 parse_error 含「降级」
    const ghostDoc = { ...REAL_DOC, status: 'completed', parse_error: '降级为关键词索引' }
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([ghostDoc]) as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    // 工具栏：幽灵入口不存在
    expect(wrapper.text()).not.toContain('重试全部降级文档')

    // 卡片动作区：只剩 查看 + 删除 两个按钮（旧实现会多出"重试向量化" = 3 个）
    const card = wrapper.findAll('.group').find((g) => g.text().includes('architecture.md'))
    expect(card).toBeTruthy()
    expect(card!.findAll('button')).toHaveLength(2)
  })
})

describe('KnowledgeView · 预览原文口径接线（PR4）', () => {
  it('legacy 响应 → 详情弹窗收到 legacy_unverified（删掉透传即红）', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
    mockedGetDocumentDetail.mockResolvedValue({
      data: { data: { id: 'doc-1', title: 'architecture.md', filename: 'architecture.md' } },
    } as never)
    mockedGetDocumentContent.mockResolvedValue({
      data: {
        data: {
          id: 'doc-1',
          filename: 'architecture.md',
          content: '【文档背景：模型摘要】\n\n第一条 旧版文本',
          text_provenance: 'legacy_unverified',
          truncated: false,
        },
      },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()
    const card = wrapper.findAll('.group').find((g) => g.text().includes('architecture.md'))
    await card!.findAll('button')[0].trigger('click')
    await flushPromises()
    await nextTick()

    const stub = wrapper.find('.detail-stub')
    expect(stub.text()).toContain('legacy_unverified')
    expect(stub.text()).toContain('full')
  })

  it('服务端完整但界面只展示前 1500 字 → preview_truncated=true', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
    mockedGetDocumentDetail.mockResolvedValue({
      data: { data: { id: 'doc-1', title: 'architecture.md', filename: 'architecture.md' } },
    } as never)
    mockedGetDocumentContent.mockResolvedValue({
      data: {
        data: {
          id: 'doc-1',
          filename: 'architecture.md',
          content: '原'.repeat(1600),
          text_provenance: 'parser_original',
          truncated: false,
        },
      },
    } as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()
    const card = wrapper.findAll('.group').find((g) => g.text().includes('architecture.md'))
    await card!.findAll('button')[0].trigger('click')
    await flushPromises()
    await nextTick()

    expect(wrapper.find('.detail-stub').text()).toContain('truncated')
  })
})
