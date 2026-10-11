/**
 * MCP 服务器管理面（只读）测试 —— 渲染卡片（状态徽标/工具名/错误折叠）/
 * 空态 / 未初始化提示 / 错误态与重试 / 只读声明。
 *
 * 变异点：
 * - 状态徽标不看真实 state（恒成功）→ 状态用例红；
 * - 工具名列表不渲染 / 错误折叠被删 → 卡片用例红；
 * - 空态不接 servers.length → 空态用例红；
 * - 未初始化不提示 → notStarted 用例红；
 * - 读取失败不显示错误原文 → 错误态用例红。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import {
  NMessageProvider,
  NAlert,
  NButton,
  NCollapse,
  NCollapseItem,
  NEmpty,
  NIcon,
  NResult,
  NSpin,
  NTag,
} from 'naive-ui'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'

import McpServersView from '../index.vue'

const apiMocks = vi.hoisted(() => ({ getMcpServers: vi.fn() }))
vi.mock('@/api/mcp', () => ({ getMcpServers: apiMocks.getMcpServers }))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const Host = defineComponent({
  render: () => h(NMessageProvider, null, { default: () => h(McpServersView) }),
})

// vitest 环境不加载 unplugin-vue-components：模板里的 n-* 需手动全局注册
// （既有页面测试同款做法），否则只渲染出未知标签、prop 型内容（description 等）丢失。
const naiveComponents = {
  NAlert, NButton, NCollapse, NCollapseItem, NEmpty, NIcon, NResult, NSpin, NTag,
}

const mountedWrappers: ReturnType<typeof mount>[] = []

/** 轮询到条件成立（消除并行跑时的时序抖动；10ms 步进，1s 上限）。 */
async function until(cond: () => boolean, timeout = 1000): Promise<void> {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (cond()) return
    await new Promise((resolve) => setTimeout(resolve, 10))
    await flushPromises()
  }
}

async function mountView() {
  const wrapper = mount(Host, { global: { plugins: [i18n], components: naiveComponents } })
  mountedWrappers.push(wrapper)
  await flushPromises()
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve, 0))
  await flushPromises()
  return wrapper
}

function makeData(overrides: Record<string, unknown> = {}) {
  return {
    started: true,
    registered_tools: 2,
    servers: [
      {
        name: 'filesystem',
        enabled: true,
        transport: 'stdio',
        description: '本地文件系统读写',
        state: 'connected',
        tools: 2,
        tool_names: ['read_file', 'write_file'],
        last_error: null,
      },
      {
        name: 'github',
        enabled: false,
        transport: 'stdio',
        description: 'GitHub API',
        state: 'disabled',
        tools: 0,
        tool_names: [],
        last_error: null,
      },
      {
        name: 'broken',
        enabled: true,
        transport: 'stdio',
        description: '',
        state: 'not_connected',
        tools: 0,
        tool_names: [],
        last_error: 'MCPConnectError: 初始化失败: FileNotFoundError: npx',
      },
    ],
    ...overrides,
  }
}

describe('MCP 服务器管理面', () => {
  afterEach(() => {
    while (mountedWrappers.length > 0) mountedWrappers.pop()!.unmount()
  })

  beforeEach(() => {
    vi.clearAllMocks()
    document.body.innerHTML = ''
    apiMocks.getMcpServers.mockResolvedValue({ data: { success: true, data: makeData() } })
  })

  it('渲染服务器卡片：状态徽标 / 工具名 / 错误折叠 / 只读声明', async () => {
    const wrapper = await mountView()
    const text = wrapper.text()

    expect(text).toContain('filesystem')
    expect(text).toContain('github')
    expect(text).toContain('本地文件系统读写')
    expect(text).toContain('已连接')
    expect(text).toContain('未启用')
    expect(text).toContain('未连接')
    expect(text).toContain('read_file')
    expect(text).toContain('write_file')
    expect(text).toContain('工具 2 个')
    expect(text).toContain('已注册动态工具 2 个')
    // 错误折叠：标题在，原文在折叠体内（n-collapse 渲染 title；内容需展开）
    expect(text).toContain('最近错误')
    // 只读声明必须出现（本页不做在线写）
    expect(text).toContain('backend/mcp_servers.json')
    expect(text).toContain('只读')
  })

  it('空态：无服务器 → n-empty + 配置指引', async () => {
    apiMocks.getMcpServers.mockResolvedValue({
      data: { success: true, data: makeData({ servers: [], registered_tools: 0 }) },
    })
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('未配置任何 MCP 服务器')
    expect(wrapper.text()).toContain('backend/mcp_servers.json')
  })

  it('未初始化：started=false 如实提示（不伪装成"无服务器"）', async () => {
    apiMocks.getMcpServers.mockResolvedValue({
      data: { success: true, data: makeData({ started: false, servers: [], registered_tools: 0 }) },
    })
    const wrapper = await mountView()
    expect(wrapper.text()).toContain('尚未初始化')
  })

  it('错误态：403 原文展示 + 重试', async () => {
    apiMocks.getMcpServers.mockRejectedValueOnce({
      response: { data: { detail: '需要管理员权限', message: '权限不足' } },
    })
    const wrapper = await mountView()
    await until(() => wrapper.text().includes('加载失败'))
    expect(wrapper.text()).toContain('加载失败')
    expect(wrapper.text()).toContain('需要管理员权限')

    apiMocks.getMcpServers.mockResolvedValue({ data: { success: true, data: makeData() } })
    const retry = Array.from(
      (wrapper.element as HTMLElement).querySelectorAll<HTMLButtonElement>('button'),
    ).find((b) => b.textContent?.includes('刷新'))
    expect(retry).toBeTruthy()
    retry!.click()
    await until(() => wrapper.text().includes('filesystem'))
    expect(apiMocks.getMcpServers).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('filesystem')
  })
})
