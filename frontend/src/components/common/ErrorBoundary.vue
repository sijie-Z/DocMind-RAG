<template>
  <div v-if="hasError" class="error-page">
    <div class="error-card">
      <!-- 图标 -->
      <div class="error-icon">
        <svg viewBox="0 0 120 120" fill="none" xmlns="http://www.w3.org/2000/svg">
          <circle cx="60" cy="60" r="54" stroke="currentColor" stroke-width="2" stroke-dasharray="8 4" opacity="0.2" />
          <circle cx="60" cy="60" r="40" stroke="currentColor" stroke-width="1.5" opacity="0.15" />
          <path d="M44 48h32M44 60h20M44 72h12" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" opacity="0.6" />
          <circle cx="60" cy="60" r="52" stroke="currentColor" stroke-width="2" opacity="0.08">
            <animate attributeName="r" values="52;58;52" dur="3s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.08;0.02;0.08" dur="3s" repeatCount="indefinite" />
          </circle>
        </svg>
      </div>

      <!-- 标题 -->
      <h1 class="error-title">页面渲染异常</h1>

      <!-- 描述 -->
      <p class="error-desc">组件加载时出现了意外错误。这通常是临时性问题，刷新即可恢复。</p>

      <!-- 错误详情 -->
      <details v-if="errorMessage" class="error-details">
        <summary>错误详情</summary>
        <pre class="error-pre">{{ errorMessage }}</pre>
      </details>

      <!-- 操作按钮 -->
      <div class="error-actions">
        <button class="btn btn--primary" @click="handleReload">
          <svg class="btn-icon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M1 4v6h6M23 20v-6h-6" />
            <path d="M20.49 9A9 9 0 005.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 013.51 15" />
          </svg>
          刷新页面
        </button>
        <button class="btn btn--ghost" @click="handleGoHome">
          返回首页
        </button>
      </div>
    </div>
  </div>
  <slot v-else />
</template>

<script setup lang="ts">
import { ref, onErrorCaptured } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const hasError = ref(false)
const errorMessage = ref('')

function formatError(err: unknown): string {
  if (!err) return '(empty error)'
  if (err instanceof Error) return err.message + (err.stack ? '\n' + err.stack : '')
  if (typeof err === 'string') return err
  try { return JSON.stringify(err, null, 2) } catch { return String(err) }
}

function triggerError(msg: string) {
  if (hasError.value) return
  errorMessage.value = msg
  hasError.value = true
}

// Vue 组件渲染错误
onErrorCaptured((err, instance, info) => {
  console.error('[ErrorBoundary] 渲染错误:', err, info)
  triggerError(formatError(err) + (info ? `\n(lifecycle: ${info})` : ''))
  return false
})

// 全局 JS 错误（在 setup 阶段立即注册，不等 onMounted）
window.addEventListener('error', (event: ErrorEvent) => {
  // 过滤浏览器良性 ResizeObserver 警告，不影响功能
  if (event.message?.includes('ResizeObserver loop completed with undelivered notifications')) {
    console.warn('[ErrorBoundary] 忽略 ResizeObserver 良性警告')
    return
  }
  console.error('[ErrorBoundary] 全局错误:', event)
  triggerError(`GlobalError: ${event.message}\n  at ${event.filename}:${event.lineno}:${event.colno}`)
})

window.addEventListener('unhandledrejection', (event: PromiseRejectionEvent) => {
  console.error('[ErrorBoundary] 未处理的 Promise 拒绝:', event.reason)
  triggerError(`UnhandledRejection: ${formatError(event.reason)}`)
})

const handleReload = () => {
  window.location.reload()
}

const handleGoHome = () => {
  router.push('/').catch(() => {
    window.location.href = '/'
  })
}
</script>

<style scoped>
.error-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f8fafc;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

.error-card {
  text-align: center;
  padding: 56px 48px 48px;
  max-width: 460px;
  width: 90%;
  background: #ffffff;
  border-radius: 20px;
  box-shadow:
    0 1px 3px rgba(0, 0, 0, 0.04),
    0 4px 16px rgba(0, 0, 0, 0.04),
    0 12px 40px rgba(0, 0, 0, 0.06);
}

.error-icon {
  width: 100px;
  height: 100px;
  margin: 0 auto 28px;
  color: #94a3b8;
}

.error-icon svg {
  width: 100%;
  height: 100%;
}

.error-title {
  font-size: 22px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 12px;
  letter-spacing: -0.3px;
}

.error-desc {
  font-size: 14px;
  color: #94a3b8;
  line-height: 1.7;
  margin-bottom: 32px;
  max-width: 320px;
  margin-left: auto;
  margin-right: auto;
}

.error-actions {
  display: flex;
  gap: 12px;
  justify-content: center;
  flex-wrap: wrap;
}

.btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 24px;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border: none;
  transition: all 0.2s ease;
  font-family: inherit;
  line-height: 1.4;
}

.btn--primary {
  background: #475569;
  color: #ffffff;
}

.btn--primary:hover {
  background: #334155;
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(71, 85, 105, 0.3);
}

.btn--ghost {
  background: transparent;
  color: #64748b;
  border: 1.5px solid #e2e8f0;
}

.btn--ghost:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.btn-icon {
  flex-shrink: 0;
}

.error-details {
  margin: 0 auto 24px;
  max-width: 360px;
  text-align: left;
  font-size: 12px;
}

.error-details summary {
  cursor: pointer;
  color: #94a3b8;
  font-size: 12px;
  margin-bottom: 8px;
}

.error-pre {
  background: #f1f5f9;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 11px;
  color: #64748b;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  font-family: 'Cascadia Code', 'Fira Code', monospace;
}

/* 暗色模式 */
@media (prefers-color-scheme: dark) {
  .error-page {
    background: #0f172a;
  }

  .error-card {
    background: #1e293b;
    box-shadow:
      0 1px 3px rgba(0, 0, 0, 0.2),
      0 4px 16px rgba(0, 0, 0, 0.3),
      0 12px 40px rgba(0, 0, 0, 0.4);
  }

  .error-icon {
    color: #64748b;
  }

  .error-title {
    color: #f1f5f9;
  }

  .error-desc {
    color: #64748b;
  }

  .btn--primary {
    background: #64748b;
  }

  .btn--primary:hover {
    background: #94a3b8;
    box-shadow: 0 4px 12px rgba(148, 163, 184, 0.3);
  }

  .btn--ghost {
    color: #94a3b8;
    border-color: #334155;
  }

  .btn--ghost:hover {
    background: #1e293b;
    border-color: #475569;
  }

  .error-pre {
    background: #0f172a;
    border-color: #334155;
    color: #94a3b8;
  }
}
</style>
