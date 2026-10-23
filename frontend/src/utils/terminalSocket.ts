/**
 * 持久终端 WebSocket 客户端（issue #477）。
 *
 * 协议（与后端 §5.5 对齐）：
 *   client → server: {type:'input',data} / {type:'resize',cols,rows} / {type:'ping'}
 *   server → client: {type:'output',data} / {type:'exit',code} /
 *                     {type:'truncated',dropped_bytes} / {type:'error',code,message} /
 *                     {type:'pong'}
 *
 * 鉴权：一次性票据走 `Sec-WebSocket-Protocol: auth.<ticket>`，绝不进 URL。
 * 每次（重）连都向 `POST /terminal/sessions/{id}/ticket` 取新票据——旧票据在
 * 签发瞬间失效，所以重连必须由本模块重新取票，不能复用建连时的那一张。
 */

import request from '@/utils/request'
import { apiUnwrap } from '@/utils/apiUnwrap'
import { getErrorMessage } from '@/utils/apiError'

export interface TerminalSessionInfo {
  terminal_id: string
  ws_path: string
  ticket: string
  expires_in: number
}

export interface TerminalSessionSummary {
  terminal_id: string
  session_id: string | null
  created_at: string
  last_activity_at: string
  status: string
}

export type TerminalServerMessage =
  | { type: 'output'; data: string }
  | { type: 'exit'; code: number }
  | { type: 'truncated'; dropped_bytes: number }
  | { type: 'error'; code: string; message: string }

export type TerminalConnectionStatus =
  | 'idle'
  | 'connecting'
  | 'connected'
  | 'reconnecting'
  | 'closed'
  | 'error'

export interface TerminalSocketOptions {
  terminalId: string
  /** 每次建连/重连都调用：返回一次性票据（后端签发新票会作废旧票）。 */
  ticketProvider: () => Promise<string>
  onMessage: (_message: TerminalServerMessage) => void
  onStatus?: (_status: TerminalConnectionStatus) => void
  onError?: (_message: string) => void
  maxReconnectAttempts?: number
}

const HEARTBEAT_INTERVAL_MS = 30000
const PONG_TIMEOUT_MS = 10000
const BASE_RECONNECT_DELAY_MS = 1000
const MAX_RECONNECT_DELAY_MS = 15000

// ── HTTP 封装（相对 /api/v1，走既有鉴权拦截器）─────────────────────────────

export async function createTerminalSession(payload: {
  session_id?: string | null
  cols?: number
  rows?: number
}): Promise<TerminalSessionInfo> {
  const response = await request.post('/terminal/sessions', payload)
  return apiUnwrap<TerminalSessionInfo>(response)
}

export async function listTerminalSessions(): Promise<TerminalSessionSummary[]> {
  const response = await request.get('/terminal/sessions')
  const data = apiUnwrap<{ sessions: TerminalSessionSummary[] }>(response)
  return data?.sessions ?? []
}

export async function issueTerminalTicket(terminalId: string): Promise<TerminalSessionInfo> {
  const response = await request.post(
    `/terminal/sessions/${encodeURIComponent(terminalId)}/ticket`,
  )
  return apiUnwrap<TerminalSessionInfo>(response)
}

export async function killTerminalSession(terminalId: string): Promise<void> {
  await request.delete(`/terminal/sessions/${encodeURIComponent(terminalId)}`)
}

/** 与 chat/notification WS 同一套 base URL 推导（DEV 走 Vite 代理）。 */
export function terminalWsUrl(terminalId: string): string {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || '/api/v1'
  let wsBaseUrl: string
  if (baseUrl.startsWith('http')) {
    wsBaseUrl = baseUrl.replace(/^http/, 'ws')
  } else {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    wsBaseUrl = `${protocol}//${host}${baseUrl.startsWith('/') ? '' : '/'}${baseUrl}`
  }
  return `${wsBaseUrl}/terminal/ws/${encodeURIComponent(terminalId)}`
}

const SERVER_MESSAGE_TYPES = new Set(['output', 'exit', 'truncated', 'error'])

export class TerminalSocket {
  private ws: WebSocket | null = null
  private stopped = true
  private attempts = 0
  private reconnectTimer: number | null = null
  private pingTimer: number | null = null
  private pongTimer: number | null = null
  private status: TerminalConnectionStatus = 'idle'

  constructor(private readonly options: TerminalSocketOptions) {}

  get currentStatus(): TerminalConnectionStatus {
    return this.status
  }

  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  async connect(): Promise<void> {
    if (
      this.ws &&
      (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)
    ) {
      return
    }
    this.stopped = false
    this.setStatus(this.attempts > 0 ? 'reconnecting' : 'connecting')

    let ticket: string
    try {
      ticket = await this.options.ticketProvider()
    } catch (error) {
      this.setStatus('error')
      this.options.onError?.(getErrorMessage(error, 'ticket request failed'))
      this.scheduleReconnect()
      return
    }
    if (this.stopped) return

    const ws = new WebSocket(terminalWsUrl(this.options.terminalId), [`auth.${ticket}`])
    this.ws = ws
    ws.onopen = () => {
      this.attempts = 0
      this.setStatus('connected')
      this.startHeartbeat()
    }
    ws.onmessage = (event: MessageEvent) => this.handleMessage(event.data)
    ws.onerror = () => {
      this.options.onError?.('websocket error')
    }
    ws.onclose = (event: CloseEvent) => {
      this.clearHeartbeat()
      this.ws = null
      if (this.stopped) {
        this.setStatus('closed')
        return
      }
      if (event.code === 1008) {
        // 握手被策略拒绝（票据无效/过期/非本人/Origin）——重试只会再失败。
        this.setStatus('error')
        this.options.onError?.(event.reason || 'policy_violation')
        return
      }
      if (event.code === 1000 && event.reason === 'terminal_exited') {
        // 进程正常退出：服务端已发 exit 帧，不再自动重连（避免重连风暴）。
        this.setStatus('closed')
        return
      }
      this.setStatus('reconnecting')
      this.scheduleReconnect()
    }
  }

  sendInput(data: string): boolean {
    if (!this.isOpen) return false
    this.ws!.send(JSON.stringify({ type: 'input', data }))
    return true
  }

  resize(cols: number, rows: number): boolean {
    if (!this.isOpen) return false
    this.ws!.send(JSON.stringify({ type: 'resize', cols, rows }))
    return true
  }

  ping(): boolean {
    if (!this.isOpen) return false
    this.ws!.send(JSON.stringify({ type: 'ping' }))
    return true
  }

  close(): void {
    this.stopped = true
    this.clearHeartbeat()
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
    const ws = this.ws
    this.ws = null
    if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
      ws.close(1000)
    }
    this.setStatus('closed')
  }

  dispose(): void {
    this.close()
  }

  private setStatus(status: TerminalConnectionStatus): void {
    if (this.status === status) return
    this.status = status
    this.options.onStatus?.(status)
  }

  private handleMessage(raw: unknown): void {
    if (typeof raw !== 'string') return
    let parsed: unknown
    try {
      parsed = JSON.parse(raw)
    } catch {
      return
    }
    if (!parsed || typeof parsed !== 'object') return
    const type = (parsed as { type?: unknown }).type
    if (typeof type !== 'string') return
    if (type === 'pong') {
      this.clearPongTimer()
      return
    }
    if (!SERVER_MESSAGE_TYPES.has(type)) return
    this.options.onMessage(parsed as TerminalServerMessage)
  }

  private startHeartbeat(): void {
    this.clearHeartbeat()
    this.pingTimer = window.setInterval(() => {
      if (!this.isOpen) return
      this.ws!.send(JSON.stringify({ type: 'ping' }))
      this.clearPongTimer()
      this.pongTimer = window.setTimeout(() => {
        // pong 超时：主动断开触发重连（一次性票据由 ticketProvider 重新获取）。
        this.ws?.close()
      }, PONG_TIMEOUT_MS)
    }, HEARTBEAT_INTERVAL_MS)
  }

  private clearHeartbeat(): void {
    if (this.pingTimer !== null) {
      window.clearInterval(this.pingTimer)
      this.pingTimer = null
    }
    this.clearPongTimer()
  }

  private clearPongTimer(): void {
    if (this.pongTimer !== null) {
      window.clearTimeout(this.pongTimer)
      this.pongTimer = null
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped || this.reconnectTimer !== null) return
    const maxAttempts = this.options.maxReconnectAttempts ?? 5
    if (this.attempts >= maxAttempts) {
      this.setStatus('error')
      this.options.onError?.('reconnect attempts exhausted')
      return
    }
    const delay = Math.min(
      BASE_RECONNECT_DELAY_MS * Math.pow(1.5, this.attempts),
      MAX_RECONNECT_DELAY_MS,
    )
    this.attempts += 1
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null
      void this.connect()
    }, delay)
  }
}
