import { onMounted, onUnmounted } from 'vue'
import { useDedupedMessage } from '@/utils/message'

export function useGlobalErrorHandler() {
  const message = useDedupedMessage()

  const handleUnhandledRejection = (event: PromiseRejectionEvent) => {
    console.error('[Unhandled Promise Rejection]', event.reason)
    message.error('发生了一些错误，请稍后重试')
  }

  const handleGlobalError = (event: ErrorEvent) => {
    if (event.message && event.message.includes('ResizeObserver')) {
      return
    }
    console.error('[Global Error]', event.error || event.message)
  }

  onMounted(() => {
    window.addEventListener('unhandledrejection', handleUnhandledRejection)
    window.addEventListener('error', handleGlobalError)
  })

  onUnmounted(() => {
    window.removeEventListener('unhandledrejection', handleUnhandledRejection)
    window.removeEventListener('error', handleGlobalError)
  })
}

export function useRequestErrorHandler() {
  const handleError = (error: any) => {
    const message = error?.message || error?.msg || '网络请求失败'
    console.error('[Request Error]', error)

    if (error?.status === 401) {
      return { needAuth: true, message: '请重新登录' }
    }
    if (error?.status === 403) {
      return { needAuth: false, message: '没有权限' }
    }
    if (error?.status === 404) {
      return { needAuth: false, message: '请求的资源不存在' }
    }
    if (error?.status >= 500) {
      return { needAuth: false, message: '服务器错误，请稍后重试' }
    }

    return { needAuth: false, message }
  }

  return { handleError }
}
