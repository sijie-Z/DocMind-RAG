/**
 * E14/C1+D1+D2 · 知识库页空态分流与错误治理判别用例。
 *
 * C1 空态三分：
 *  - 空库（无搜索）→ 「暂无知识库文件」+ 上传第一个文件；
 *  - 搜索无命中（search 非空）→ 「未找到匹配的文档」+ 清除搜索（不得再劝上传）；
 *  - 错误 → 错误态 + 重试。
 * D1：删除失败只弹一次 toast，且用后端 detail 原文（axios 传输层英文文案禁止上屏）。
 * D2：搜索失败清空旧结果（不展示陈旧卡片/统计），提供重试入口。
 *
 * 变异点：
 * - 空态回到单一「暂无知识库文件」→ 搜索无命中用例红；
 * - catch 不清 knowledgeBases → 搜索失败清旧结果用例红；
 * - 页面 catch 回到 error.message → 删除失败文案用例红；
 * - 重试按钮失效 → 重试用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import naive, { NDialogProvider, NMessageProvider } from 'naive-ui'

import KnowledgeView from '../index.vue'
import { getKnowledgeBases, deleteKnowledgeBase } from '@/api/knowledge'
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

const messageSpies = vi.hoisted(() => ({
  success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn(), loading: vi.fn(),
}))
vi.mock('@/utils/message', () => ({ useDedupedMessage: () => messageSpies }))

const mockedGetKnowledgeBases = vi.mocked(getKnowledgeBases)
const mockedDelete = vi.mocked(deleteKnowledgeBase)

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
  status: 'parsed',
}

const listResponse = (docs: unknown[]) => ({
  data: {
    success: true,
    message: '获取成功',
    data: { data: docs, total: docs.length, page: 1, page_size: 12 },
  },
})

/** axios 错误形状（isAxiosError + config 齐备，与真实 axios reject 同构） */
function axiosError(status: number, data: unknown, message: string) {
  return Object.assign(new Error(message), {
    isAxiosError: true,
    config: { url: '/x' },
    response: status === 0 ? undefined : { status, data },
  })
}

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const Host = defineComponent({
  render: () =>
    h(NDialogProvider, null, {
      default: () => h(NMessageProvider, null, { default: () => h(KnowledgeView) }),
    }),
})

function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const user = useUserStore()
  user.userInfo = {
    id: 1, username: 'tester', email: 't@t', isAdmin: false,
    organization_id: 3, organizations: [], createdAt: '', updatedAt: '',
  } as UserInfo

  return mount(Host, {
    global: {
      plugins: [naive, pinia, i18n],
      stubs: {
        KnowledgeUploadPanel: true,
        KnowledgeDetailModals: true,
      },
    },
  })
}

function findButtonByText(wrapper: ReturnType<typeof mountView>, text: string) {
  return wrapper.findAll('button').find((b) => b.text().includes(text))
}

const vmOf = (wrapper: ReturnType<typeof mountView>) =>
  wrapper.findComponent(KnowledgeView).vm as unknown as {
    loadKnowledgeBases: () => Promise<void>
    deleteKnowledge: (item: typeof REAL_DOC) => void
    searchText: string
    knowledgeBases: unknown[]
  }

/** 输入搜索词并等过 300ms 防抖 + 请求结算 */
async function search(wrapper: ReturnType<typeof mountView>, text: string) {
  await wrapper.find('input').setValue(text)
  await sleep(350)
  await flushPromises()
  await nextTick()
}

beforeEach(() => {
  mockedGetKnowledgeBases.mockReset()
  mockedDelete.mockReset()
  messageSpies.error.mockReset()
  messageSpies.success.mockReset()
  messageSpies.warning.mockReset()
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('KnowledgeView · C1 空态分流', () => {
  it('空库（无搜索词）→ 上传引导；不冒充「搜索无命中」', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([]) as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain(zh.knowledge.empty)
    expect(wrapper.text()).toContain(zh.knowledge.uploadFirst)
    expect(wrapper.text()).not.toContain(zh.knowledge.searchEmptyTitle)
  })

  it('搜索无命中 → 「未找到匹配的文档」+ 清除搜索；不劝上传', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([]) as never)

    const wrapper = mountView()
    await flushPromises()
    await search(wrapper, '不存在的文档')

    expect(wrapper.text()).toContain(zh.knowledge.searchEmptyTitle)
    expect(wrapper.text()).toContain('不存在的文档')
    expect(findButtonByText(wrapper, zh.knowledge.clearSearch), '应提供清除搜索入口').toBeTruthy()
    // 空库的「上传第一个文件」引导不得出现在搜索无命中态
    expect(wrapper.text()).not.toContain(zh.knowledge.uploadFirst)
  })

  it('清除搜索 → 回到无搜索列表（请求参数 search 清空）', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([]) as never)

    const wrapper = mountView()
    await flushPromises()
    await search(wrapper, 'architecture')

    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
    await findButtonByText(wrapper, zh.knowledge.clearSearch)!.trigger('click')
    await flushPromises()
    await nextTick()

    const lastParams = mockedGetKnowledgeBases.mock.calls.at(-1)?.[0] as { search?: string }
    expect(lastParams.search ?? '').toBe('')
    expect(wrapper.text()).toContain('architecture.md')
  })
})

describe('KnowledgeView · D2 搜索失败清旧结果 + 重试', () => {
  it('搜索请求失败 → 清空旧结果与统计、展示错误态与重试；重试成功恢复', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)

    const wrapper = mountView()
    await flushPromises()
    await nextTick()
    expect(wrapper.text()).toContain('architecture.md')

    mockedGetKnowledgeBases.mockRejectedValueOnce(
      axiosError(500, { detail: '内部堆栈 /srv/app/x.py:42' }, 'Request failed with status code 500'),
    )
    await search(wrapper, 'architecture')

    expect(wrapper.text()).toContain(zh.knowledge.loadFailed)
    // 旧结果必须清空（不得残留陈旧卡片/状态；错误态虽优先渲染，内存状态也须如实）
    expect(wrapper.text()).not.toContain('architecture.md')
    expect(vmOf(wrapper).knowledgeBases, '失败后知识库列表状态必须清空').toEqual([])
    // 统计卡不得残留旧数字（用占位符如实表示未知）
    expect(wrapper.text()).toContain('—')
    // 5xx 内部细节禁止上屏
    expect(wrapper.text()).not.toContain('/srv/app/x.py')

    const retry = findButtonByText(wrapper, zh.common.retry)
    expect(retry, '错误态应提供重试按钮').toBeTruthy()

    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
    await retry!.trigger('click')
    await flushPromises()
    await nextTick()

    expect(wrapper.text()).toContain('architecture.md')
    expect(wrapper.text()).not.toContain(zh.knowledge.loadFailed)
  })
})

describe('KnowledgeView · D1 错误文案单一通道', () => {
  it('删除失败 → 只弹一次 toast，展示后端 detail；不回显 axios 英文原文', async () => {
    mockedGetKnowledgeBases.mockResolvedValue(listResponse([REAL_DOC]) as never)
    const wrapper = mountView()
    await flushPromises()
    await nextTick()

    mockedDelete.mockRejectedValueOnce(
      axiosError(400, { detail: '文档不存在或已被删除' }, 'Request failed with status code 400'),
    )

    vmOf(wrapper).deleteKnowledge(REAL_DOC)
    await flushPromises()
    await nextTick()

    const confirm = Array.from(document.body.querySelectorAll('button')).find(
      (b) => b.textContent?.trim() === zh.common.confirm,
    )
    expect(confirm, '确认对话框应打开').toBeTruthy()
    confirm!.click()
    await flushPromises()
    await nextTick()

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const text = String(messageSpies.error.mock.calls[0][0])
    expect(text).toContain('文档不存在或已被删除')
    expect(text).not.toContain('Request failed with status code')
  })
})
