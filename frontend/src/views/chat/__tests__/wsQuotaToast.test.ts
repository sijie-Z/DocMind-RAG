/**
 * 批次十四：WS 聊天配额错误帧 → 明确四语 toast（不是后端中文 content / 网络错误）。
 *
 * 帧契约（backend/app/api/v1/endpoints/chat.py WebSocket 分支）：
 *   {type:"error", code:"ORG_TOKEN_QUOTA_EXCEEDED", content:"…", detail:{used,quota,reset_at}}
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'

import zh from '@/locales/zh'

const { messageSpies, handlers } = vi.hoisted(() => ({
  messageSpies: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
  handlers: new Map<string, (_data: Record<string, unknown>) => void>(),
}))

vi.mock('@/utils/message', () => ({
  useDedupedMessage: () => messageSpies,
}))

vi.mock('@/utils/websocket', () => ({
  wsService: {
    off: vi.fn((event: string) => handlers.delete(event)),
    on: vi.fn((event: string, handler: (_data: Record<string, unknown>) => void) =>
      handlers.set(event, handler),
    ),
    connect: vi.fn(),
    disconnect: vi.fn(),
    sendStop: vi.fn(),
  },
}))

import { useChatConnection } from '../composables/useChatConnection'
import { useUserStore } from '@/stores/user'

const i18n = createI18n({ legacy: false, locale: 'zh', messages: { zh } })

function mountConnection() {
  setActivePinia(createPinia())
  const user = useUserStore()
  user.token = 'tok'
  user.userInfo = { id: 1, username: 'u' } as never

  let connection!: ReturnType<typeof useChatConnection>
  const Host = defineComponent({
    setup() {
      connection = useChatConnection(
        { value: [] } as never,
        () => {},
        async () => {},
        () => 'g',
      )
      return () => null
    },
  })
  mount(Host, { global: { plugins: [i18n] } })
  return connection
}

describe('useChatConnection · WS 配额错误帧', () => {
  beforeEach(() => {
    handlers.clear()
    messageSpies.error.mockClear()
  })

  it('ORG_TOKEN_QUOTA_EXCEEDED → 四语配额文案（used/quota/resetAt），不回显后端中文', async () => {
    const conn = mountConnection()
    conn.useSSE.value = false
    await conn.connectWebSocket()

    const onError = handlers.get('error')
    expect(onError).toBeTruthy()
    onError!({
      type: 'error',
      code: 'ORG_TOKEN_QUOTA_EXCEEDED',
      content: '组织本月 token 配额已用尽',
      detail: { used: 150, quota: 100, reset_at: '2026-12-01T00:00:00+00:00' },
    })

    expect(messageSpies.error).toHaveBeenCalledTimes(1)
    const text = String(messageSpies.error.mock.calls[0][0])
    expect(text).toContain('150')
    expect(text).toContain('100')
    expect(text).toContain('配额')
    expect(text).not.toBe('组织本月 token 配额已用尽')
  })

  it('普通 error 帧（无配额码）保持后端 content 兜底', async () => {
    const conn = mountConnection()
    conn.useSSE.value = false
    await conn.connectWebSocket()

    handlers.get('error')!({ type: 'error', content: '普通错误' })
    expect(messageSpies.error).toHaveBeenCalledWith('普通错误')
  })
})
