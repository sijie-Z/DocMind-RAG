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
import { NMessageProvider, NDialogProvider, NCheckbox } from 'naive-ui'

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
      // n-checkbox 生产由 app.use(naive) 全局注册；测试里注册真组件以驱动勾选
      components: { NCheckbox },
      stubs: {
        KnowledgeUploadPanel: true,
        KnowledgeDetailModals: true,
        Tooltip: SlotStub('Tooltip'),
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
    expect(mockedBatchDelete).toHaveBeenCalledWith(['doc-1'], 3)
  })

  it('无当前组织（超管场景）：请求仍发出、organization_id 传 null 由 api 层省略', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
    mockedBatchDelete.mockResolvedValue(undefined as never)

    const wrapper = mountView()
    const user = useUserStore()
    user.userInfo = { ...(user.userInfo as UserInfo), organization_id: null } as UserInfo

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

    expect(mockedBatchDelete).toHaveBeenCalledWith(['doc-1'], null)
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
