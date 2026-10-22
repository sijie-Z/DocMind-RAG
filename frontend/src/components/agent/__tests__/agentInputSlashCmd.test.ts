/**
 * User Commands (#475) · AgentInput 斜杠菜单判别测试。
 *
 * 菜单 = 3 条本地模板（search/doc/workflow）+ 服务端命令（GET /commands 的
 * help/status/export/tools）。模板选中后仍展开本地提示词；服务端命令选中后
 * emit('command', line)，由视图调用 POST /commands/execute —— 不进入聊天流。
 *
 * 变异点（任一成立本文件对应用例红）：
 * - 菜单不再合并 GET /commands 的命令（仍为 5 条本地项）→ 7 条用例红；
 * - 服务端命令选中后仍插入模板/走 send 而非 emit command → 执行用例红；
 * - 过滤只看模板 id、不看命令名/别名 → 别名/命令前缀用例红；
 * - enabled=false / 请求失败时不显示如实提示 → 可用性用例红；
 * - 重新引入 clear 本地项 → 条目集合用例红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import zh from '@/locales/zh'
import AgentInput from '@/components/agent/AgentInput.vue'

const { listMock } = vi.hoisted(() => ({
  listMock: vi.fn(),
}))

vi.mock('@/api/commands', () => ({
  commandsApi: {
    list: listMock,
    execute: vi.fn(),
  },
}))

vi.mock('@/api/knowledge', () => ({
  getKnowledgeBases: vi.fn().mockResolvedValue({ data: { data: { data: [] } } }),
}))

const i18n = createI18n({ legacy: false, locale: 'zh', fallbackLocale: 'zh', messages: { zh } })

const SERVER_COMMANDS = [
  {
    name: 'help',
    aliases: ['h'],
    description: '列出可用命令或查看某个命令的帮助',
    description_i18n: { zh: '列出可用命令或查看某个命令的帮助', en: 'List available commands' },
    usage: '/help [命令名]',
    permission: 'user',
    args: [{ name: 'command', description: '命令名', required: false, choices: [], default: null }],
  },
  {
    name: 'status',
    aliases: [],
    description: '查看当前会话与最近任务状态',
    description_i18n: { zh: '查看当前会话与最近任务状态', en: 'Show session and task status' },
    usage: '/status',
    permission: 'user',
    args: [],
  },
  {
    name: 'export',
    aliases: [],
    description: '导出当前会话（Markdown / JSON）',
    description_i18n: { zh: '导出当前会话（Markdown / JSON）', en: 'Export the session' },
    usage: '/export [markdown|json]',
    permission: 'user',
    args: [
      { name: 'format', description: '导出格式', required: false, choices: ['markdown', 'json'], default: 'markdown' },
    ],
  },
  {
    name: 'tools',
    aliases: [],
    description: '列出 Agent 可用工具与默认禁用状态',
    description_i18n: { zh: '列出 Agent 可用工具与默认禁用状态', en: 'List agent tools' },
    usage: '/tools',
    permission: 'user',
    args: [],
  },
]

const okList = (overrides: Record<string, unknown> = {}) => ({
  data: {
    success: true,
    data: { enabled: true, scope: 'global', commands: SERVER_COMMANDS, ...overrides },
  },
})

function mountInput(props: Record<string, unknown> = {}) {
  return mount(AgentInput, {
    props: { placeholder: '输入', ...props },
    global: { plugins: [i18n] },
  })
}

async function typeCmd(wrapper: ReturnType<typeof mountInput>, text: string) {
  await wrapper.find('textarea').setValue(text)
  await flushPromises()
}

function optionNames(wrapper: ReturnType<typeof mountInput>) {
  return wrapper.findAll('[data-testid="cmd-option"]').map((o) => o.attributes('data-cmd-name'))
}

describe('AgentInput · 斜杠命令菜单（#475 后端驱动）', () => {
  beforeEach(() => {
    listMock.mockReset()
    listMock.mockResolvedValue(okList())
  })

  it('挂载即拉取命令（带 sessionId 作用域）；输入 / 展示 7 条：3 模板 + 4 服务端命令', async () => {
    const wrapper = mountInput({ sessionId: 's-1' })
    await flushPromises()
    expect(listMock).toHaveBeenCalledWith('s-1')

    await typeCmd(wrapper, '/')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(true)
    const options = wrapper.findAll('[data-testid="cmd-option"]')
    expect(options).toHaveLength(7)
    expect(optionNames(wrapper).slice(0, 3)).toEqual(['search', 'doc', 'workflow'])
    expect(optionNames(wrapper).slice(3)).toEqual(['help', 'status', 'export', 'tools'])
    expect(options[3].text()).toContain('/help')
    expect(options[3].text()).toContain('列出可用命令')
    expect(options.find((o) => o.text().includes('/clear'))).toBeUndefined()
  })

  it('前缀过滤同时作用于模板名、命令名与别名：/se→search，/st→status，/h→help', async () => {
    const wrapper = mountInput()
    await flushPromises()

    await typeCmd(wrapper, '/se')
    expect(optionNames(wrapper)).toEqual(['search'])

    await typeCmd(wrapper, '/st')
    expect(optionNames(wrapper)).toEqual(['status'])

    await typeCmd(wrapper, '/h')
    expect(optionNames(wrapper)).toEqual(['help'])
  })

  it('模板 search 选中 → 仍展开本地提示词（不走服务端）', async () => {
    const wrapper = mountInput()
    await flushPromises()
    await typeCmd(wrapper, '/search 预算')
    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()

    expect(wrapper.emitted('command')).toBeUndefined()
    expect(wrapper.emitted('send')).toBeUndefined()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe(
      '联网搜索并总结：预算',
    )
  })

  it('服务端命令 help 选中 → emit command(/help)，不发聊天、不清屏残留', async () => {
    const wrapper = mountInput()
    await flushPromises()
    await typeCmd(wrapper, '/help')
    await wrapper.find('[data-cmd-name="help"]').trigger('mousedown')
    await flushPromises()

    const emitted = wrapper.emitted('command')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toBe('/help')
    expect(wrapper.emitted('send')).toBeUndefined()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(false)
  })

  it('命令参数保留：/export json 选中 → emit command(/export json)', async () => {
    const wrapper = mountInput()
    await flushPromises()
    await typeCmd(wrapper, '/export json')
    await wrapper.find('[data-cmd-name="export"]').trigger('mousedown')
    await flushPromises()

    expect(wrapper.emitted('command')![0][0]).toBe('/export json')
  })

  it('键盘 ↓ 切换高亮、Enter 选中高亮项（跨模板/命令同一序列）', async () => {
    const wrapper = mountInput()
    await flushPromises()
    await typeCmd(wrapper, '/')
    await wrapper.find('textarea').trigger('keydown.down')
    const options = wrapper.findAll('[data-testid="cmd-option"]')
    expect(options[1].classes()).toContain('active')

    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe('基于知识库回答：')
  })

  it('负例：/xyz 无匹配 → Enter 发送原文（不拦截）', async () => {
    const wrapper = mountInput()
    await flushPromises()
    await typeCmd(wrapper, '/xyz 随便')
    expect(wrapper.find('[data-testid="cmd-empty"]').exists()).toBe(true)
    await wrapper.find('textarea').trigger('keydown.enter')
    await flushPromises()
    const emitted = wrapper.emitted('send')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toBe('/xyz 随便')
  })

  it('enabled=false：只保留 3 条模板并如实提示服务端命令已停用', async () => {
    listMock.mockResolvedValue(okList({ enabled: false, commands: [] }))
    const wrapper = mountInput()
    await flushPromises()
    await typeCmd(wrapper, '/')
    expect(optionNames(wrapper)).toEqual(['search', 'doc', 'workflow'])
    const hint = wrapper.find('[data-testid="cmd-unavailable"]')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('服务端命令已停用')
  })

  it('命令列表加载失败：模板仍可用，并显示如实错误提示', async () => {
    listMock.mockRejectedValue(new Error('network'))
    const wrapper = mountInput()
    await flushPromises()
    await typeCmd(wrapper, '/')
    expect(optionNames(wrapper)).toEqual(['search', 'doc', 'workflow'])
    expect(wrapper.find('[data-testid="cmd-error"]').exists()).toBe(true)
  })

  it('负例：不以 / 开头的文本不触发菜单；Escape 关菜单', async () => {
    const wrapper = mountInput()
    await flushPromises()
    await typeCmd(wrapper, '普通消息')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(false)

    await typeCmd(wrapper, '/sea')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(true)
    await wrapper.find('textarea').trigger('keydown.esc')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(false)
  })

  it('负例：steering 模式不开放命令菜单', async () => {
    const wrapper = mountInput({ steering: true })
    await flushPromises()
    await typeCmd(wrapper, '/')
    expect(wrapper.find('[data-testid="cmd-menu"]').exists()).toBe(false)
  })

  it('会话切换竞态：旧会话的命令响应不覆盖新会话清单', async () => {
    let resolveFirst: (value: unknown) => void = () => {}
    const firstPending = new Promise((resolve) => {
      resolveFirst = resolve
    })
    listMock.mockImplementationOnce(() => firstPending)
    listMock.mockResolvedValueOnce(
      okList({ commands: [SERVER_COMMANDS[0]] }), // 新会话只有 help
    )

    const wrapper = mountInput({ sessionId: 's-1' })
    await wrapper.setProps({ sessionId: 's-2' })
    await flushPromises()

    // 旧会话响应后到：必须被丢弃
    resolveFirst(okList())
    await flushPromises()

    await typeCmd(wrapper, '/')
    expect(optionNames(wrapper)).toEqual(['search', 'doc', 'workflow', 'help'])
  })
})
