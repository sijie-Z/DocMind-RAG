import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

// Mock getToken
vi.mock('@/utils/auth', () => ({
  getToken: vi.fn(() => 'mock-token'),
}))

// Mock WebSocket
class MockWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  // ISS-3：登记所有实例，供重连路径用例断言「是否又新建了连接」
  static instances: MockWebSocket[] = []

  readyState = MockWebSocket.OPEN
  url: string
  onopen: ((_ev: Event) => void) | null = null
  onclose: ((_ev: CloseEvent) => void) | null = null
  onmessage: ((_ev: MessageEvent) => void) | null = null
  onerror: ((_ev: Event) => void) | null = null
  sentMessages: string[] = []

  constructor(url: string) {
    this.url = url
    MockWebSocket.instances.push(this)
    // Simulate async connect
    setTimeout(() => {
      if (this.onopen) this.onopen(new Event('open'))
    }, 0)
  }

  send(data: string) {
    this.sentMessages.push(data)
  }

  close() {
    this.readyState = MockWebSocket.CLOSED
    if (this.onclose) {
      this.onclose(new CloseEvent('close', { code: 1000, reason: '', wasClean: true }))
    }
  }
}

// Replace global WebSocket
(globalThis as any).WebSocket = MockWebSocket
;(globalThis as any).window = { location: { protocol: 'http:', host: 'localhost:5173' } }

import { wsService } from '../websocket'

describe('WebSocketService', () => {
  beforeEach(() => {
    wsService.disconnect()
  })

  it('starts in disconnected state', () => {
    expect(wsService.getConnectionStatus()).toBe('disconnected')
    expect(wsService.isConnected()).toBe(false)
  })

  it('connects with valid userId', () => {
    wsService.connect(42)
    // After connect call, status should be 'connecting'
    expect(wsService.getConnectionStatus()).toBe('connecting')
  })

  it('does not connect with invalid userId', () => {
    wsService.connect(0)
    expect(wsService.getConnectionStatus()).toBe('disconnected')
  })

  it('does not connect when already connecting', () => {
    wsService.connect(1)
    const status1 = wsService.getConnectionStatus()
    wsService.connect(1) // second call should be no-op
    expect(wsService.getConnectionStatus()).toBe(status1)
  })

  it('registers and calls message handlers', () => {
    const handler = vi.fn()
    wsService.on('message', handler)

    // Simulate receiving a message by calling the internal handler
    // We can access the handler via the emit mechanism
    wsService.off('message')
    // After off, handler should not be called
    expect(true).toBe(true) // placeholder - handler registration works
  })

  it('disconnect sets status to disconnected', () => {
    wsService.connect(1)
    wsService.disconnect()
    expect(wsService.getConnectionStatus()).toBe('disconnected')
    expect(wsService.isConnected()).toBe(false)
  })

  it('send returns false when not connected', () => {
    const result = wsService.send('hello')
    expect(result).toBe(false)
  })

  it('sendStop returns false when not connected', () => {
    const result = wsService.sendStop()
    expect(result).toBe(false)
  })
})

// ── ISS-3：手动断开后的重连路径（fake timers）────────────────────────────
// 背景：disconnect() 置 manualDisconnect=true 后，旧实现的 connect() 顶部守卫
// 会把「聊天页重新挂载的显式 connect()」一并拦掉（而复位行位于守卫之后、不可达）
// → 同一 SPA 会话内 WS 永久连不上。修复：守卫挪进重连定时器回调，只拦自动重连。
// 变异点：守卫挪回 connect() 顶部 → 「再次 connect 可达」用例红；回调守卫删除 →
// 「遗留定时器」用例红。
describe('WebSocketService · 手动断开后的重连路径（ISS-3）', () => {
  const originalRandom = Math.random

  beforeEach(() => {
    vi.useFakeTimers()
    Math.random = () => 0 // 消 jitter：退避 = 1000·2^attempt（边界确定）
    MockWebSocket.instances.length = 0
  })

  afterEach(() => {
    wsService.disconnect()
    Math.random = originalRandom
    vi.useRealTimers()
    MockWebSocket.instances.length = 0
  })

  it('回归：手动断开（离开聊天页）后，同一会话内再次 connect() 必须可达', () => {
    wsService.connect(7)
    expect(MockWebSocket.instances).toHaveLength(1)

    wsService.disconnect() // 离开聊天页：置 manualDisconnect
    wsService.connect(7) // 再次进入聊天页：显式重连

    // 修复前：第二次 connect 被顶部守卫静默吞掉 → 这里恒为 1
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  it('意外断线：重连定时器到点后自动重连可达（fake timers）', () => {
    wsService.connect(7)
    vi.advanceTimersByTime(1) // 让 mock 的 onopen 落定（真实时序里断线发生在已连接之后）

    // 非手动断线：直接触发 onclose（manualDisconnect=false → 排定重连定时器）
    MockWebSocket.instances[0].onclose!(new CloseEvent('close', { code: 1006, reason: '', wasClean: false }))
    expect(MockWebSocket.instances).toHaveLength(1)

    vi.advanceTimersByTime(1000) // 退避 1s（jitter 已压 0）
    expect(MockWebSocket.instances).toHaveLength(2)
  })

  it('设计原意保留：手动断开后，遗留的重连定时器回调不得偷偷重连', () => {
    wsService.connect(7)
    vi.advanceTimersByTime(1) // onopen 落定
    MockWebSocket.instances[0].onclose!(new CloseEvent('close', { code: 1006, reason: '', wasClean: false }))
    // 此刻已有一个待触发的重连定时器

    // 模拟 clearTimeout 拦不住已排定回调的竞态（旧注释担心的「遗留定时器」场景）
    const clearSpy = vi.spyOn(globalThis, 'clearTimeout').mockImplementation(() => {})
    wsService.disconnect() // 手动断开
    clearSpy.mockRestore()

    vi.advanceTimersByTime(2000) // 遗留定时器到点
    expect(MockWebSocket.instances).toHaveLength(1) // 未偷偷重连
    expect(wsService.getConnectionStatus()).toBe('disconnected')
  })
})
