<template>
  <div class="terminal-panel">
    <div class="terminal-toolbar">
      <span class="terminal-status" :class="`status-${status}`">
        <span class="status-dot" aria-hidden="true"></span>
        {{ statusText }}
      </span>
      <span class="terminal-toolbar-spacer"></span>
      <n-button size="tiny" secondary :disabled="!terminalId" @click="copySelection">
        {{ t('agent.terminal.copy') }}
      </n-button>
      <n-button size="tiny" secondary :disabled="!terminalId" @click="pasteClipboard">
        {{ t('agent.terminal.paste') }}
      </n-button>
      <n-button size="tiny" secondary :disabled="!terminalId" @click="clearScreen">
        {{ t('agent.terminal.clear') }}
      </n-button>
      <n-button size="tiny" secondary :loading="busy" @click="openSession">
        {{ t('agent.terminal.reconnect') }}
      </n-button>
      <n-button size="tiny" secondary :disabled="!terminalId" @click="closeTerminal">
        {{ t('agent.terminal.close') }}
      </n-button>
    </div>
    <div ref="screenRef" class="terminal-screen" data-testid="terminal-screen"></div>
    <p v-if="errorDetail" class="terminal-note terminal-note--error">{{ errorDetail }}</p>
    <p v-else-if="notice" class="terminal-note">{{ notice }}</p>
  </div>
</template>

<script setup lang="ts">
/**
 * 持久终端面板（issue #477）：xterm.js + FitAddon + 一次性票据 WS。
 *
 * 生命周期：面板挂载 → POST /terminal/sessions 建会话 → WS 连接；
 * 面板卸载 / 关闭按钮 → DELETE 会话（释放配额）。断线自动重连，每次重连
 * 都经 ticketProvider 重新领一次性票据（旧票在签发时即失效）。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'
import { getErrorMessage } from '@/utils/apiError'
import {
  TerminalSocket,
  createTerminalSession,
  issueTerminalTicket,
  killTerminalSession,
  type TerminalConnectionStatus,
  type TerminalServerMessage,
} from '@/utils/terminalSocket'

const props = withDefaults(
  defineProps<{
    sessionId?: string | null
  }>(),
  { sessionId: null },
)

const { t } = useI18n()

const screenRef = ref<HTMLElement | null>(null)
const terminalId = ref('')
const status = ref<TerminalConnectionStatus>('idle')
const busy = ref(false)
const errorDetail = ref('')
const notice = ref('')

let term: Terminal | null = null
let fitAddon: FitAddon | null = null
let socket: TerminalSocket | null = null
let resizeObserver: ResizeObserver | null = null
let unmounted = false

const statusText = computed(() => t(`agent.terminal.status.${status.value}`))

function handleServerMessage(message: TerminalServerMessage): void {
  if (message.type === 'output') {
    term?.write(message.data)
    return
  }
  if (message.type === 'truncated') {
    notice.value = t('agent.terminal.truncated', { bytes: message.dropped_bytes })
    return
  }
  if (message.type === 'exit') {
    notice.value = t('agent.terminal.exited', { code: message.code })
    status.value = 'closed'
    return
  }
  if (message.type === 'error') {
    errorDetail.value = message.message || message.code
  }
}

function connectSocket(id: string): void {
  socket?.close()
  socket = new TerminalSocket({
    terminalId: id,
    ticketProvider: async () => (await issueTerminalTicket(id)).ticket,
    onMessage: handleServerMessage,
    onStatus: (next) => {
      status.value = next
      if (next === 'connected' && term) {
        socket?.resize(term.cols, term.rows)
      }
    },
    onError: (detail) => {
      errorDetail.value = detail
    },
  })
  void socket.connect()
}

async function releaseTerminal(id: string): Promise<void> {
  if (!id) return
  try {
    await killTerminalSession(id)
  } catch {
    // 会话可能已被空闲回收 / 后端不可用；释放失败不影响本地收尾。
  }
}

/** 新建会话并连接（首次挂载与「重连」按钮共用：重连 = 换一个干净终端）。 */
async function openSession(): Promise<void> {
  if (busy.value) return
  busy.value = true
  errorDetail.value = ''
  notice.value = ''
  const previousId = terminalId.value
  socket?.close()
  socket = null
  terminalId.value = ''
  term?.reset()
  try {
    if (previousId) await releaseTerminal(previousId)
    const info = await createTerminalSession({
      session_id: props.sessionId,
      cols: term?.cols ?? 80,
      rows: term?.rows ?? 24,
    })
    if (unmounted) {
      void releaseTerminal(info.terminal_id)
      return
    }
    terminalId.value = info.terminal_id
    connectSocket(info.terminal_id)
    fit()
  } catch (error) {
    status.value = 'error'
    errorDetail.value = getErrorMessage(error, t('agent.terminal.createFailed'))
  } finally {
    busy.value = false
  }
}

async function closeTerminal(): Promise<void> {
  socket?.close()
  socket = null
  const id = terminalId.value
  terminalId.value = ''
  term?.reset()
  status.value = 'closed'
  notice.value = t('agent.terminal.sessionClosed')
  await releaseTerminal(id)
}

async function copySelection(): Promise<void> {
  const selection = term?.getSelection() ?? ''
  if (!selection) return
  try {
    await navigator.clipboard.writeText(selection)
  } catch {
    notice.value = t('agent.terminal.copyFailed')
  }
}

async function pasteClipboard(): Promise<void> {
  try {
    const text = await navigator.clipboard.readText()
    if (text) socket?.sendInput(text)
  } catch {
    notice.value = t('agent.terminal.pasteFailed')
  }
}

function clearScreen(): void {
  term?.clear()
  notice.value = ''
}

function fit(): void {
  try {
    fitAddon?.fit()
  } catch {
    // 抽屉动画中容器尺寸可能为 0 —— 下一次 ResizeObserver 会再试。
  }
}

onMounted(() => {
  if (!screenRef.value) return
  term = new Terminal({
    cursorBlink: true,
    fontSize: 13,
    fontFamily: "'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace",
    scrollback: 5000,
    theme: {
      background: '#0f172a',
      foreground: '#e5e7eb',
      cursor: '#93c5fd',
    },
  })
  fitAddon = new FitAddon()
  term.loadAddon(fitAddon)
  term.open(screenRef.value)
  fit()
  term.onData((data) => {
    socket?.sendInput(data)
  })
  term.onResize(({ cols, rows }) => {
    socket?.resize(cols, rows)
  })
  resizeObserver = new ResizeObserver(() => fit())
  resizeObserver.observe(screenRef.value)
  void openSession()
})

onBeforeUnmount(() => {
  unmounted = true
  resizeObserver?.disconnect()
  resizeObserver = null
  socket?.dispose()
  socket = null
  void releaseTerminal(terminalId.value)
  terminalId.value = ''
  term?.dispose()
  term = null
  fitAddon = null
})
</script>

<style scoped>
.terminal-panel {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 140px);
  min-height: 320px;
  background: var(--card-bg, #ffffff);
  border: 1px solid var(--border-color, #e5e7eb);
  border-radius: 10px;
  overflow: hidden;
}

.dark .terminal-panel {
  background: var(--card-bg-dark, #1f2937);
  border-color: var(--border-color-dark, #374151);
}

.terminal-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-bottom: 1px solid var(--border-color, #e5e7eb);
  font-size: 12px;
}

.dark .terminal-toolbar {
  border-color: var(--border-color-dark, #374151);
}

.terminal-toolbar-spacer {
  flex: 1;
}

.terminal-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--text-secondary, #6b7280);
}

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #9ca3af;
}

.status-connected .status-dot {
  background: #22c55e;
}

.status-connecting .status-dot,
.status-reconnecting .status-dot {
  background: #f59e0b;
}

.status-error .status-dot {
  background: #ef4444;
}

.terminal-screen {
  flex: 1;
  min-height: 0;
  padding: 8px;
  background: #0f172a;
}

.terminal-note {
  margin: 0;
  padding: 4px 10px;
  font-size: 12px;
  color: var(--text-secondary, #6b7280);
  border-top: 1px solid var(--border-color, #e5e7eb);
}

.terminal-note--error {
  color: #ef4444;
}

.dark .terminal-note {
  border-color: var(--border-color-dark, #374151);
}
</style>
