/**
 * E2 · 个人设置「下线设备」二次确认判别用例。
 *
 * 验收口径（E2 五处破坏性操作之五）：
 *  - 未确认（取消）→ revokeMySession 不得被调用，会话（设备）列表不变；
 *  - 确认后 → 调用接口并刷新列表；
 *  - 确认弹窗经 Naive UI dialog.warning 弹出（与 API Key 确认同法）。
 *
 * 变异点：去掉确认直接下线 → 负相用例红（接口被调用）。
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
  revokeSession: vi.fn(),
  getMySessions: vi.fn(),
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
    getMySessions: apiMocks.getMySessions,
    getMyActivityLogs: vi.fn().mockResolvedValue({ data: [] }),
    updateUserProfile: vi.fn(),
    updatePassword: vi.fn(),
    uploadAvatar: vi.fn(),
    revokeMySession: apiMocks.revokeSession,
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

const SESSION = {
  id: 7,
  device: 'Chrome / Windows',
  ip_address: '127.0.0.1',
  user_agent: 'Mozilla/5.0',
  is_active: true,
  created_at: '2026-10-18T10:00:00',
  last_active_at: '2026-10-18T10:05:00',
}

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
  apiMocks.revokeSession.mockReset().mockResolvedValue({ data: {} })
  apiMocks.getMySessions.mockReset().mockResolvedValue({ data: [] })
})

describe('个人设置 · 下线设备二次确认（E2）', () => {
  it('负相（取消）：不调下线接口，设备列表不变', async () => {
    mountHarness()
    await flushPromises()
    capturedApi.sessions.value = [SESSION as never]

    dialogState.decision = 'cancel'
    await capturedApi.handleRevokeSession(SESSION.id)
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(apiMocks.revokeSession).not.toHaveBeenCalled()
    expect(capturedApi.sessions.value.map((s) => s.id)).toEqual([SESSION.id])
  })

  it('正相（确认）：调用下线接口并刷新设备列表', async () => {
    mountHarness()
    await flushPromises()
    capturedApi.sessions.value = [SESSION as never]

    dialogState.decision = 'confirm'
    apiMocks.getMySessions.mockResolvedValue({ data: [] })
    await capturedApi.handleRevokeSession(SESSION.id)
    await flushPromises()

    expect(dialogWarning).toHaveBeenCalledTimes(1)
    expect(apiMocks.revokeSession).toHaveBeenCalledWith(SESSION.id)
    expect(apiMocks.getMySessions).toHaveBeenCalled()
  })
})
