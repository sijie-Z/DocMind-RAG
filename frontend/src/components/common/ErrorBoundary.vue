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
/* H4：颜色全部收敛为 --eb-* 令牌；暗色覆盖一律写扁平选择器。
   旧写法把暗色规则写成 html.dark 下的嵌套块，依赖原生 CSS 嵌套——构建链
   未配置嵌套降级，低版本 WebView 会整块丢弃，暗色下退回硬编码亮白。
   此文件不得再出现嵌套形式的暗色块。 */
.error-page {
  --eb-page-bg: #f8fafc;
  --eb-card-bg: #ffffff;
  --eb-card-shadow:
    0 1px 3px rgba(0, 0, 0, 0.04),
    0 4px 16px rgba(0, 0, 0, 0.04),
    0 12px 40px rgba(0, 0, 0, 0.06);
  --eb-icon: #94a3b8;
  --eb-title: #1e293b;
  --eb-desc: #94a3b8;
  --eb-primary-bg: #475569;
  --eb-primary-fg: #ffffff;
  --eb-primary-bg-hover: #334155;
  --eb-primary-shadow: rgba(71, 85, 105, 0.3);
  --eb-ghost-fg: #64748b;
  --eb-ghost-border: #e2e8f0;
  --eb-ghost-bg-hover: #f1f5f9;
  --eb-ghost-border-hover: #cbd5e1;
  --eb-pre-bg: #f1f5f9;
  --eb-pre-border: #e2e8f0;
  --eb-pre-fg: #64748b;

  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--eb-page-bg);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* 暗色覆盖：只变令牌，不散落色值（扁平选择器，勿改回嵌套块） */
html.dark .error-page {
  --eb-page-bg: #0f172a;
  --eb-card-bg: #1e293b;
  --eb-card-shadow:
    0 1px 3px rgba(0, 0, 0, 0.2),
    0 4px 16px rgba(0, 0, 0, 0.3),
    0 12px 40px rgba(0, 0, 0, 0.4);
  --eb-icon: #64748b;
  --eb-title: #f1f5f9;
  /* 正文对比度：旧 #64748b 对 #1e293b 仅约 3.0:1，提到 #94a3b8（约 5.7:1） */
  --eb-desc: #94a3b8;
  --eb-primary-bg: #64748b;
  --eb-primary-fg: #ffffff;
  --eb-primary-bg-hover: #94a3b8;
  --eb-primary-shadow: rgba(148, 163, 184, 0.3);
  --eb-ghost-fg: #94a3b8;
  --eb-ghost-border: #334155;
  --eb-ghost-bg-hover: #1e293b;
  --eb-ghost-border-hover: #475569;
  --eb-pre-bg: #0f172a;
  --eb-pre-border: #334155;
  --eb-pre-fg: #94a3b8;
}

.error-card {
  text-align: center;
  padding: 56px 48px 48px;
  max-width: 460px;
  width: 90%;
  background: var(--eb-card-bg);
  border-radius: 20px;
  box-shadow: var(--eb-card-shadow);
}

.error-icon {
  width: 100px;
  height: 100px;
  margin: 0 auto 28px;
  color: var(--eb-icon);
}

.error-icon svg {
  width: 100%;
  height: 100%;
}

.error-title {
  font-size: 22px;
  font-weight: 600;
  color: var(--eb-title);
  margin-bottom: 12px;
  letter-spacing: -0.3px;
}

.error-desc {
  font-size: 14px;
  color: var(--eb-desc);
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
  background: var(--eb-primary-bg);
  color: var(--eb-primary-fg);
}

.btn--primary:hover {
  background: var(--eb-primary-bg-hover);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px var(--eb-primary-shadow);
}

.btn--ghost {
  background: transparent;
  color: var(--eb-ghost-fg);
  border: 1.5px solid var(--eb-ghost-border);
}

.btn--ghost:hover {
  background: var(--eb-ghost-bg-hover);
  border-color: var(--eb-ghost-border-hover);
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
  color: var(--eb-desc);
  font-size: 12px;
  margin-bottom: 8px;
}

.error-pre {
  background: var(--eb-pre-bg);
  border: 1px solid var(--eb-pre-border);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 11px;
  color: var(--eb-pre-fg);
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 200px;
  overflow-y: auto;
  font-family: 'Cascadia Code', 'Fira Code', monospace;
}
</style>
