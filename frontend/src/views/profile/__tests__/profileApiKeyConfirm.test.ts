/**
 * T12 · API Key 生成 / 重生成 / 撤销 二次确认用例。
 *
 * 验收口径：
 *  - 三个操作在调用接口前必须弹 Naive UI dialog.warning 确认；
 *  - 未确认（取消/关闭）→ 对应接口不得被调用；
 *  - 确认后 → 接口被调用；重生成确认文案必须写明「旧 Key 立即失效」。
 *
 * 测试相：mock useDialog 可控返回 false（取消）/ true（确认）两相。
 * 变异点：任一操作去掉确认 → 负相用例红（接口被调用）；重生成文案缺失关键提示 → 红。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createI18n } from 'vue-i18n'
import { createPinia, setActivePinia } from 'pinia'
import { NMessageProvider } from 'naive-ui'

import { useProfilePage } from '../composables/useProfilePage'
import zh from '@/locales/zh'

interface DialogOptions {
  title?: string
  content?: string
  onPositiveClick?: () => unknown
  onNegativeClick?: () => unknown
  onClose?: () => unknown
}

const { dialogState, dialogWarning } = vi.hoisted(() => {
  const state = {
    decision: 'cancel' as 'cancel' | 'confirm',
    calls: [] as DialogOptions[],
  }
  const warning = vi.fn((opts: DialogOptions) => {
    state.calls.push(opts)
    if (state.decision === 'confirm') opts.onPositiveClick?.()
    else opts.onNegativeClick?.()
  })
  return { dialogState: state, dialogWarning: warning }
})

const apiMocks = vi.hoisted(() => ({
  generate: vi.fn(),
  revoke: vi.fn(),
}))

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useDialog: () => ({ warning: dialogWarning, info: vi.fn() }),
  }
})

vi.mock('@/api/user', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/user')>()
  return {
    ...actual,
    getUserProfile: vi.fn().mockResolvedValue({
      data: {
        id: 1, username: 'tester', email: 't@t', nickname: 'tester', full_name: null,
        phone: null, avatar_url: null, bio: null, organization_id: 3, role: 'user',
        is_active: true, created_at: '2026-10-08T05:39:53', last_login_at: null,
      },
    }),
    getUserStats: vi.fn().mockResolvedValue({
      data: { conversation_count: 0, message_count: 0, file_count: 0, knowledge_count: 0, storage_used: 0 },
    }),
    getUserActivities: vi.fn().mockResolvedValue({ data: [] }),
    getMySessions: vi.fn().mockResolvedValue({ data: [] }),
    getMyActivityLogs: vi.fn().mockResolvedValue({ data: [] }),
    updateUserProfile: vi.fn(),
    updatePassword: vi.fn(),
    uploadAvatar: vi.fn(),
    generateApiKey: apiMocks.generate,
    revokeApiKey: apiMocks.revoke,
    revokeMySession: vi.fn(),
  }
})

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/utils/echarts', () => ({
  default: {
    init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })),
  },
}))

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
  ok: true,
  json: async () => ({ providers: ['deepseek'], current: null }),
}))

let capturedApi: ReturnType<typeof useProfilePage>

const Harness = defineComponent({
  setup() {
    capturedApi = useProfilePage()
    return () => null
  },
})

function mountHarness() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const Host = defineComponent({
    render: () => h(NMessageProvider, null, { default: () => h(Harness) }),
  })
  return mount(Host, { global: { plugins: [pinia, i18n] } })
}

beforeEach(() => {
  dialogState.decision = 'cancel'
  dialogState.calls.length = 0
  dialogWarning.mockClear()
  apiMocks.generate.mockReset().mockResolvedValue({ data: { api_key: 'sk-new' } })
  apiMocks.revoke.mockReset().mockResolvedValue({ data: {} })
})

describe('useProfilePage · API Key 操作二次确认（T12）', () => {
  it('负相（取消）：生成 / 重生成 / 撤销均不调用接口', async () => {
    mountHarness()
    await flushPromises()
    dialogState.decision = 'cancel'

    // 生成（无既有 Key）
    capturedApi.apiKey.value = ''
    await capturedApi.generateApiKey()
    expect(apiMocks.generate).not.toHaveBeenCalled()

    // 重生成（有既有 Key）
    capturedApi.apiKey.value = 'sk-old'
    await capturedApi.generateApiKey()
    expect(apiMocks.generate).not.toHaveBeenCalled()

    // 撤销
    await capturedApi.revokeApiKey()
    expect(apiMocks.revoke).not.toHaveBeenCalled()

    // 三个操作都先经过 dialog.warning 确认
    expect(dialogWarning).toHaveBeenCalledTimes(3)
  })

  it('正相（确认）：生成调用接口并落 Key；重生成提示「旧 Key 立即失效」', async () => {
    mountHarness()
    await flushPromises()
    dialogState.decision = 'confirm'

    // 生成（无既有 Key）
    capturedApi.apiKey.value = ''
    await capturedApi.generateApiKey()
    expect(apiMocks.generate).toHaveBeenCalledTimes(1)
    expect(capturedApi.apiKey.value).toBe('sk-new')

    // 重生成（有既有 Key）：确认文案必须写明旧 Key 立即失效
    capturedApi.apiKey.value = 'sk-old'
    dialogState.calls.length = 0
    await capturedApi.generateApiKey()
    expect(apiMocks.generate).toHaveBeenCalledTimes(2)
    const lastCall = dialogState.calls[dialogState.calls.length - 1]
    expect(String(lastCall?.content)).toContain('旧 Key 立即失效')
  })

  it('正相（确认）：撤销调用接口并清空 Key', async () => {
    mountHarness()
    await flushPromises()
    dialogState.decision = 'confirm'

    capturedApi.apiKey.value = 'sk-old'
    await capturedApi.revokeApiKey()
    expect(apiMocks.revoke).toHaveBeenCalledTimes(1)
    expect(capturedApi.apiKey.value).toBe('')
  })
})
