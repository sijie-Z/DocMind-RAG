import { useMessage, type MessageReactive } from 'naive-ui'

const activeMessages = new Map<string, MessageReactive>()
const DEBOUNCE_MS = 1500

function dedupeKey(type: string, content: string): string {
  return `${type}:${content}`
}

export function useDedupedMessage() {
  const message = useMessage()

  const show = (type: 'success' | 'error' | 'warning' | 'info', content: string, duration = 3000) => {
    const key = dedupeKey(type, content)

    // 如果已有相同消息，先销毁
    if (activeMessages.has(key)) {
      const existing = activeMessages.get(key)!
      try { existing.destroy() } catch {}
      activeMessages.delete(key)
    }

    const msg = message[type](content, { duration })
    activeMessages.set(key, msg)

    setTimeout(() => {
      activeMessages.delete(key)
    }, DEBOUNCE_MS)

    return msg
  }

  return {
    success: (content: string) => show('success', content),
    error: (content: string) => show('error', content, 5000),
    warning: (content: string) => show('warning', content),
    info: (content: string) => show('info', content),
    loading: (content: string) => message.loading(content),
  }
}

export default useDedupedMessage
