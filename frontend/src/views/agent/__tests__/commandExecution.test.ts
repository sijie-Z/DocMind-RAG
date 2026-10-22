/**
 * User Commands (#475) · 视图接线判别：菜单命令执行走 POST /commands/execute，
 * 结果以命令卡片进入消息流（不进入聊天流）；/export 可下载；失败如实展示。
 *
 * 变异点：
 * - handleCommand 改回 agentApi.chat（slash 文本进 PER）→ 执行用例红；
 * - 不渲染 CommandResultCard → 结果/下载用例红；
 * - 失败分支吞掉后端 message / 状态 → 错误用例红；
 * - 导出按钮不调用 downloadTextFile → 下载用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { NMessageProvider, NDialogProvider, NSpin, NButton, NTooltip, NIcon } from 'naive-ui'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import zh from '@/locales/zh'

import AgentView from '../index.vue'
import { useAgentStore } from '@/stores/agent'

const { listMock, executeMock, downloadMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
  executeMock: vi.fn(),
  downloadMock: vi.fn(),
}))

vi.mock('@/api/commands', () => ({
  commandsApi: { list: listMock, execute: executeMock },
}))

vi.mock('@/utils/artifact', () => ({
  downloadTextFile: downloadMock,
  artifactFilename: vi.fn(),
  isUnifiedDiff: vi.fn(() => false),
  looksLikeJson: vi.fn(() => false),
  looksLikeMarkdown: vi.fn(() => false),
  detectArtifactKind: vi.fn(() => 'text'),
}))

const apiMocks = vi.hoisted(() => ({
  listTools: vi.fn().mockResolvedValue({ data: { success: true, data: [] } }),
  listSessions: vi.fn().mockResolvedValue({ data: { success: true, data: { sessions: [] } } }),
  getConfig: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
  chat: vi.fn(),
  getSession: vi.fn().mockResolvedValue({ data: { success: true, data: {} } }),
  deleteSession: vi.fn().mockResolvedValue({}),
  createSession: vi.fn().mockResolvedValue({ data: { success: true, data: null } }),
  updateConfig: vi.fn().mockResolvedValue({}),
  submitFeedback: vi.fn().mockResolvedValue({}),
  getTask: vi.fn().mockResolvedValue({ data: { success: true, data: { state: 'completed' } } }),
  cancelTask: vi.fn().mockResolvedValue({ data: { state: 'cancel_requested' } }),
  decideApproval: vi.fn().mockResolvedValue({ abort: () => {} }),
  decidePlanApproval: vi.fn(),
  listTasks: vi.fn().mockResolvedValue({ data: { success: true, data: { tasks: [] } } }),
  getReplay: vi.fn(),
  steerTask: vi.fn(),
}))

vi.mock('@/api/agent', () => ({ agentApi: apiMocks }))

vi.mock('@/api/sessions', () => ({
  sessionsApi: { fork: vi.fn(), lineage: vi.fn() },
}))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () =>
    h(NMessageProvider, null, {
      default: () => h(NDialogProvider, null, { default: () => h(AgentView) }),
    }),
})

const stubs = {
  PlanTree: true,
  ThinkingStream: true,
  ToolCallCard: true,
  ReflectionBanner: true,
  AgentConfigPanel: true,
  SessionSelector: true,
  Markdown: true,
  RunReportBlock: true,
  RunStats: true,
  ReplayTimeline: true,
  RecentTaskList: true,
  ApprovalCard: true,
  PlanApprovalCard: true,
  NSpin: true,
  NButton: true,
  NTooltip: true,
  NIcon: true,
  // 注意：AgentInput / CommandResultCard 不 stub —— 交互与结果渲染要真组件。
}

const COMMANDS = [
  {
    name: 'help',
    aliases: ['h'],
    description: '列出可用命令或查看某个命令的帮助',
    description_i18n: { zh: '列出可用命令或查看某个命令的帮助', en: 'List commands' },
    usage: '/help',
    permission: 'user',
    args: [],
  },
  {
    name: 'export',
    aliases: [],
    description: '导出当前会话',
    description_i18n: { zh: '导出当前会话', en: 'Export session' },
    usage: '/export [format]',
    permission: 'user',
    args: [],
  },
]

async function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAgentStore()
  store.setCurrentSession('s-1')
  const wrapper = mount(Host, {
    global: {
      plugins: [pinia, i18n],
      components: { NSpin, NButton, NTooltip, NIcon },
      stubs,
    },
  })
  await flushPromises()
  return { wrapper, store }
}

async function runMenuCommand(wrapper: Awaited<ReturnType<typeof mountView>>['wrapper'], line: string) {
  const textarea = wrapper.find('textarea')
  await textarea.setValue(line)
  await flushPromises()
  await textarea.trigger('keydown.enter')
  await flushPromises()
}

describe('AgentView · User Commands 执行接线', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    listMock.mockResolvedValue({
      data: { success: true, data: { enabled: true, scope: 'global', commands: COMMANDS } },
    })
    executeMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          command: 'help',
          line: '/help',
          scope: 'global',
          success: true,
          content: '## 可用命令（4）\n- `/help` — 帮助',
          data: { commands: [] },
          args: {},
          error_code: '',
          error_message: '',
          error_field: '',
          duration_ms: 2,
        },
      },
    })
  })

  it('选中服务端命令 → POST /commands/execute（带会话），不进入聊天流', async () => {
    const { wrapper } = await mountView()
    await runMenuCommand(wrapper, '/help')

    expect(executeMock).toHaveBeenCalledTimes(1)
    expect(executeMock).toHaveBeenCalledWith('/help', 's-1')
    expect(apiMocks.chat).not.toHaveBeenCalled()

    const card = wrapper.find('[data-testid="command-result-card"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('/help')
    const resultCard = wrapper.findComponent({ name: 'CommandResultCard' })
    expect((resultCard.props('terminal') as { content: string }).content).toContain('可用命令')
    wrapper.unmount()
  })

  it('命令失败（409 并发）→ 卡片如实展示后端文案与失败状态', async () => {
    executeMock.mockRejectedValue({
      response: {
        status: 409,
        data: {
          success: false,
          code: 409,
          error_code: 'COMMAND_BUSY',
          message: '同一会话已有命令在执行',
          detail: {},
        },
      },
    })
    const { wrapper } = await mountView()
    await runMenuCommand(wrapper, '/help')

    const card = wrapper.find('[data-testid="command-result-card"]')
    expect(card.exists()).toBe(true)
    expect(card.text()).toContain('同一会话已有命令在执行')
    expect(wrapper.find('[data-testid="command-status"]').text()).toContain('失败')
    expect(apiMocks.chat).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('/export 成功 → 结果卡片提供下载，文件名/内容/类型来自后端结果', async () => {
    executeMock.mockResolvedValue({
      data: {
        success: true,
        data: {
          command: 'export',
          line: '/export',
          scope: 'global',
          success: true,
          content: '# 会话标题\n\n导出内容',
          data: { format: 'markdown', filename: '会话标题.md', message_count: 3, truncated: false },
          args: { format: 'markdown' },
          error_code: '',
          error_message: '',
          error_field: '',
          duration_ms: 5,
        },
      },
    })
    const { wrapper } = await mountView()
    await runMenuCommand(wrapper, '/export')

    const download = wrapper.find('[data-testid="command-export"]')
    expect(download.exists()).toBe(true)
    await download.trigger('click')
    expect(downloadMock).toHaveBeenCalledWith('会话标题.md', '# 会话标题\n\n导出内容', 'markdown')
    wrapper.unmount()
  })
})
