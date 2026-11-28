<template>
  <!-- 公开知识库只读检索页（批次十三）：无登录、无生成、无内部字段。
       token 即全部权限；撤销/过期/禁用/不存在统一显示「不可用」。 -->
  <div class="public-kb-page">
    <header class="public-kb-page-header">
      <div class="public-kb-brand">DocMind</div>
      <div class="public-kb-heading">
        <h1 class="public-kb-page-title">
          {{ meta?.title || t('publicKb.public.title') }}
        </h1>
        <p v-if="meta?.description" class="public-kb-page-desc" data-testid="public-kb-description">
          {{ meta.description }}
        </p>
        <p v-if="meta?.expires_at" class="public-kb-page-expiry" data-testid="public-kb-expires-at">
          {{ t('publicKb.expiresAt', { time: formatTime(meta.expires_at) }) }}
        </p>
      </div>
    </header>

    <main class="public-kb-page-main">
      <div v-if="loading" class="public-kb-page-state" role="status" data-testid="public-kb-loading">
        {{ t('publicKb.loading') }}
      </div>
      <div
        v-else-if="loadError"
        class="public-kb-page-error"
        role="alert"
        data-testid="public-kb-load-error"
      >
        <span>{{ t('publicKb.public.loadFailed') }}</span>
        <button class="public-kb-retry" data-testid="public-kb-retry" @click="retryLoad">
          {{ t('publicKb.retry') }}
        </button>
      </div>
      <div
        v-else-if="unavailable"
        class="public-kb-page-error"
        role="alert"
        data-testid="public-kb-unavailable"
      >
        {{ t('publicKb.public.unavailable') }}
      </div>
      <template v-else>
        <form class="public-kb-search" @submit.prevent="runSearch">
          <input
            v-model="query"
            class="public-kb-search-input"
            data-testid="public-kb-search-input"
            :placeholder="t('publicKb.public.searchPlaceholder')"
            maxlength="200"
            :aria-label="t('publicKb.public.searchPlaceholder')"
          />
          <button
            class="public-kb-search-btn"
            type="submit"
            data-testid="public-kb-search-submit"
            :disabled="searching"
          >
            {{ searching ? t('publicKb.public.searching') : t('publicKb.public.search') }}
          </button>
        </form>

        <p v-if="searchError" class="public-kb-page-error" role="alert" data-testid="public-kb-search-error">
          {{ t('publicKb.public.loadFailed') }}
        </p>

        <template v-else-if="searched">
          <p class="public-kb-result-meta" data-testid="public-kb-result-total">
            {{ t('publicKb.public.resultCount', { n: results.length }) }}
          </p>
          <div
            v-if="results.length === 0"
            class="public-kb-page-state"
            data-testid="public-kb-empty"
          >
            <p>{{ t('publicKb.public.empty') }}</p>
            <p class="public-kb-result-hint">{{ t('publicKb.public.emptyHint') }}</p>
          </div>
          <ul v-else class="public-kb-results" data-testid="public-kb-results">
            <li
              v-for="item in results"
              :key="item.rank"
              class="public-kb-result"
              :data-testid="`public-kb-result-${item.rank}`"
            >
              <div class="public-kb-result-head">
                <span class="public-kb-result-rank">#{{ item.rank }}</span>
                <span class="public-kb-result-title">{{ item.title }}</span>
                <span class="public-kb-result-score">{{ item.score.toFixed(3) }}</span>
              </div>
              <p class="public-kb-result-snippet">{{ item.snippet }}</p>
            </li>
          </ul>
        </template>

        <!-- 批次十七：匿名聊天（仅当服务端投影 chat_enabled=true 时展示）。 -->
        <section v-if="meta?.chat_enabled" class="public-kb-chat" data-testid="public-kb-chat">
          <header class="public-kb-chat-header">
            <h2 class="public-kb-chat-title">{{ t('publicKb.public.chatTitle') }}</h2>
            <p class="public-kb-chat-hint">{{ t('publicKb.public.chatHint') }}</p>
          </header>

          <div
            class="public-kb-chat-log"
            role="log"
            aria-live="polite"
            data-testid="public-kb-chat-log"
          >
            <p
              v-if="chatMessages.length === 0"
              class="public-kb-page-state"
              data-testid="public-kb-chat-empty"
            >
              {{ t('publicKb.public.chatEmpty') }}
            </p>
            <div
              v-for="(msg, index) in chatMessages"
              :key="index"
              class="public-kb-chat-msg"
              :class="`is-${msg.role}`"
              :data-testid="`public-kb-chat-msg-${index}`"
            >
              <div class="public-kb-chat-role">
                {{
                  msg.role === 'user'
                    ? t('publicKb.public.chatYou')
                    : t('publicKb.public.chatAssistant')
                }}
              </div>
              <p v-if="msg.content" class="public-kb-chat-content">{{ msg.content }}</p>
              <p
                v-else-if="msg.role === 'assistant' && msg.state === 'streaming'"
                class="public-kb-chat-content public-kb-chat-pending"
                data-testid="public-kb-chat-pending"
              >
                {{ t('publicKb.public.chatSending') }}
              </p>
              <p
                v-else-if="msg.role === 'assistant' && msg.state === 'done'"
                class="public-kb-chat-content public-kb-chat-pending"
                data-testid="public-kb-chat-no-answer"
              >
                {{ t('publicKb.public.chatNoAnswer') }}
              </p>
              <p
                v-else-if="msg.role === 'assistant' && msg.state === 'stopped'"
                class="public-kb-chat-content public-kb-chat-pending"
                data-testid="public-kb-chat-stopped"
              >
                {{ t('publicKb.public.chatStopped') }}
              </p>

              <div
                v-if="msg.role === 'assistant' && msg.sources.length > 0"
                class="public-kb-chat-sources"
                data-testid="public-kb-chat-sources"
              >
                <div class="public-kb-chat-sources-title">
                  {{ t('publicKb.public.chatSources') }}
                </div>
                <ul class="public-kb-chat-source-list">
                  <li
                    v-for="source in msg.sources"
                    :key="source.rank"
                    class="public-kb-chat-source"
                  >
                    <div class="public-kb-result-head">
                      <span class="public-kb-result-rank">#{{ source.rank }}</span>
                      <span class="public-kb-result-title">{{ source.title }}</span>
                      <span class="public-kb-result-score">{{ Number(source.score ?? 0).toFixed(3) }}</span>
                    </div>
                    <p class="public-kb-result-snippet">{{ source.snippet }}</p>
                  </li>
                </ul>
              </div>
            </div>
          </div>

          <p
            v-if="chatErrorText"
            class="public-kb-page-error"
            role="alert"
            data-testid="public-kb-chat-error"
          >
            {{ chatErrorText }}
          </p>

          <form class="public-kb-chat-form" @submit.prevent="sendChat">
            <input
              v-model="chatInput"
              class="public-kb-search-input"
              data-testid="public-kb-chat-input"
              :placeholder="t('publicKb.public.chatPlaceholder')"
              maxlength="200"
              :aria-label="t('publicKb.public.chatPlaceholder')"
              :disabled="chatBusy"
            />
            <button
              v-if="!chatBusy"
              class="public-kb-search-btn"
              type="submit"
              data-testid="public-kb-chat-send"
              :disabled="!chatInput.trim()"
            >
              {{ t('publicKb.public.chatSend') }}
            </button>
            <button
              v-else
              class="public-kb-chat-stop"
              type="button"
              data-testid="public-kb-chat-stop"
              @click="stopChat"
            >
              {{ t('publicKb.public.chatStop') }}
            </button>
          </form>
        </section>
      </template>
    </main>

    <footer class="public-kb-page-footer">{{ t('publicKb.public.poweredBy') }}</footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import {
  publicKbPublicApi,
  PublicKbChatError,
  type PublicKbChatHistoryItem,
  type PublicKbChatSource,
  type PublicKbMeta,
  type PublicKbSearchHit,
} from '@/api/publicKb'

const route = useRoute()
const { t } = useI18n()

const meta = ref<PublicKbMeta | null>(null)
const loading = ref(true)
const unavailable = ref(false)
const loadError = ref(false)
const query = ref('')
const searching = ref(false)
const searched = ref(false)
const results = ref<PublicKbSearchHit[]>([])
const searchError = ref(false)

interface ChatUiMessage {
  role: 'user' | 'assistant'
  content: string
  sources: PublicKbChatSource[]
  /** streaming=流式进行中；done=收到 done；stopped=用户停止；error=失败。 */
  state: 'streaming' | 'done' | 'stopped' | 'error'
}

const chatMessages = ref<ChatUiMessage[]>([])
const chatInput = ref('')
const chatBusy = ref(false)
const chatErrorCode = ref('')
const chatErrorDetail = ref<Record<string, unknown> | null>(null)
const chatAbort = ref<AbortController | null>(null)
// 代际计数：token 切换/卸载后，旧请求的回调不得再写入已清空的数组。
let chatGeneration = 0
const CHAT_HISTORY_LIMIT = 6
// 与后端 CHAT_HISTORY_CONTENT_MAX_CHARS 对齐：超长回答必须截断后再作为
// 历史发送，否则后端会整体拒绝（400），后续每一轮都会失败。
const CHAT_HISTORY_CONTENT_LIMIT = 2000

/** 只有 404 才代表「链接不存在/失效」；502/429/网络错误必须是可重试错误态。 */
function isNotFound(error: unknown): boolean {
  const candidate = error as { response?: { status?: number }; status?: number }
  const status = candidate?.response?.status ?? candidate?.status
  return status === 404
}

function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString()
}

async function load(token: string) {
  loading.value = true
  unavailable.value = false
  loadError.value = false
  meta.value = null
  searched.value = false
  results.value = []
  searchError.value = false
  chatGeneration += 1
  chatAbort.value?.abort()
  chatAbort.value = null
  chatMessages.value = []
  chatInput.value = ''
  chatBusy.value = false
  chatErrorCode.value = ''
  chatErrorDetail.value = null
  try {
    meta.value = await publicKbPublicApi.get(token)
  } catch (error) {
    if (isNotFound(error)) unavailable.value = true
    else loadError.value = true
  } finally {
    loading.value = false
  }
}

function retryLoad() {
  const token = String(route.params.token || '')
  if (token) void load(token)
}

async function runSearch() {
  const token = String(route.params.token || '')
  if (!token || searching.value) return
  const text = query.value.trim()
  if (!text) {
    searched.value = true
    results.value = []
    return
  }
  searching.value = true
  searchError.value = false
  try {
    const data = await publicKbPublicApi.search(token, text, 10)
    results.value = data.results
    searched.value = true
  } catch (error) {
    if (isNotFound(error)) {
      // 会话期间链接被撤销/过期：与「不可用」同形，不区分原因。
      unavailable.value = true
    } else {
      // 502/429/网络故障：保留搜索框，显示可重试错误，绝不谎称链接已失效。
      searchError.value = true
    }
  } finally {
    searching.value = false
  }
}

function isAbortError(error: unknown): boolean {
  if (error instanceof DOMException && error.name === 'AbortError') return true
  // 兼容 polyfill/跨 realm：只认显式 name，不回退成网络错误。
  return (error as { name?: string } | null)?.name === 'AbortError'
}

function buildChatHistory(): PublicKbChatHistoryItem[] {
  return chatMessages.value
    .filter((message) => message.content.trim().length > 0)
    .slice(-CHAT_HISTORY_LIMIT)
    .map((message) => ({
      role: message.role,
      content: message.content.slice(0, CHAT_HISTORY_CONTENT_LIMIT),
    }))
}

const chatErrorText = computed(() => {
  const code = chatErrorCode.value
  if (!code) return ''
  const detail = chatErrorDetail.value || {}
  const used = Number(detail.used ?? 0)
  const budget = Number(detail.budget ?? 0)
  const quota = Number(detail.quota ?? 0)
  const resetAt = typeof detail.reset_at === 'string' ? formatTime(detail.reset_at) : ''
  if (code === 'PUBLIC_KB_CHAT_BUDGET_EXCEEDED') {
    return t('publicKb.public.chatBudgetExceeded', { used, budget, resetAt })
  }
  if (code === 'ORG_TOKEN_QUOTA_EXCEEDED') {
    return t('publicKb.public.chatOrgQuota', { used, quota, resetAt })
  }
  if (code === 'RATE_LIMIT_EXCEEDED') return t('publicKb.public.chatRateLimited')
  if (code === 'CHAT_UNAVAILABLE') return t('publicKb.public.chatUnavailable')
  return t('publicKb.public.chatFailed')
})

async function sendChat() {
  const token = String(route.params.token || '')
  const message = chatInput.value.trim()
  if (!token || !message || chatBusy.value) return
  const generation = chatGeneration
  const history = buildChatHistory()
  chatErrorCode.value = ''
  chatErrorDetail.value = null
  chatMessages.value.push({ role: 'user', content: message, sources: [], state: 'done' })
  const assistantIndex =
    chatMessages.value.push({
      role: 'assistant',
      content: '',
      sources: [],
      state: 'streaming',
    }) - 1
  chatInput.value = ''
  chatBusy.value = true
  const controller = new AbortController()
  chatAbort.value = controller
  let terminal = false
  try {
    await publicKbPublicApi.chat(
      token,
      { message, history },
      {
        onSources: (sources) => {
          if (generation !== chatGeneration) return
          chatMessages.value[assistantIndex].sources = sources
        },
        onDelta: (delta) => {
          if (generation !== chatGeneration) return
          chatMessages.value[assistantIndex].content += delta
        },
        onDone: () => {
          if (generation !== chatGeneration) return
          terminal = true
          chatMessages.value[assistantIndex].state = 'done'
        },
        onError: (error) => {
          if (generation !== chatGeneration) return
          terminal = true
          chatMessages.value[assistantIndex].state = 'error'
          chatErrorCode.value = error.code
          chatErrorDetail.value =
            error.detail && typeof error.detail === 'object'
              ? (error.detail as Record<string, unknown>)
              : null
        },
      },
      { signal: controller.signal },
    )
  } catch (error) {
    if (generation !== chatGeneration) return
    if (isAbortError(error)) {
      // 用户主动停止：保留已生成内容，不把取消显示成错误。
      chatMessages.value[assistantIndex].state = 'stopped'
    } else if (error instanceof PublicKbChatError) {
      chatMessages.value[assistantIndex].state = 'error'
      chatErrorCode.value = error.status === 404 ? 'CHAT_UNAVAILABLE' : error.code
      chatErrorDetail.value =
        error.detail && typeof error.detail === 'object'
          ? (error.detail as Record<string, unknown>)
          : null
    } else {
      chatMessages.value[assistantIndex].state = 'error'
      chatErrorCode.value = 'NETWORK_ERROR'
      chatErrorDetail.value = null
    }
  } finally {
    if (generation !== chatGeneration) return
    chatBusy.value = false
    chatAbort.value = null
    const assistant = chatMessages.value[assistantIndex]
    if (assistant) {
      if (!terminal && assistant.state === 'streaming') {
        // 流在 done/error 之前结束（代理截断/服务端崩溃）：不要伪装成完整回答。
        assistant.state = 'error'
        chatErrorCode.value = chatErrorCode.value || 'STREAM_INCOMPLETE'
      }
      // 失败且没有任何内容/来源：移除空助手气泡（错误已在下方统一展示）；
      // 用户主动停止且无内容：同样移除；收到 done 的空回答保留以显示“无结果”。
      if (
        !assistant.content &&
        assistant.sources.length === 0 &&
        (assistant.state === 'error' || assistant.state === 'stopped')
      ) {
        chatMessages.value.splice(assistantIndex, 1)
      }
    }
  }
}

function stopChat() {
  chatAbort.value?.abort()
}

onBeforeUnmount(() => {
  // 路由离开/组件卸载时终止匿名流，避免后台继续生成并消耗日预算。
  chatGeneration += 1
  chatAbort.value?.abort()
})

// SPA 内从一个公开链接导航到另一个时重新拉取（组件复用）。
watch(
  () => String(route.params.token || ''),
  (token) => {
    if (token) void load(token)
  },
  { immediate: true },
)

onMounted(() => {
  if (!route.params.token) loading.value = false
})
</script>

<style scoped>
.public-kb-page {
  min-height: 100vh;
  background: #f8fafc;
  color: #0f172a;
  display: flex;
  flex-direction: column;
}
.dark .public-kb-page {
  background: #020617;
  color: #e2e8f0;
}
.public-kb-page-header {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 18px 20px;
  border-bottom: 1px solid #e2e8f0;
  background: #fff;
}
.dark .public-kb-page-header {
  border-color: #1e293b;
  background: #0f172a;
}
.public-kb-brand {
  font-weight: 800;
  letter-spacing: 0.02em;
  color: #1d4ed8;
  white-space: nowrap;
}
.dark .public-kb-brand {
  color: #93c5fd;
}
.public-kb-page-title {
  font-size: 22px;
  font-weight: 800;
}
.public-kb-page-desc {
  margin-top: 4px;
  font-size: 14px;
  color: #475569;
  white-space: pre-wrap;
}
.dark .public-kb-page-desc {
  color: #94a3b8;
}
.public-kb-page-expiry {
  margin-top: 4px;
  font-size: 12px;
  color: #b45309;
}
.public-kb-page-main {
  width: min(880px, 100%);
  margin: 0 auto;
  padding: 24px 20px 40px;
  flex: 1;
}
.public-kb-page-state {
  padding: 18px 0;
  color: #64748b;
  font-size: 14px;
}
.public-kb-page-error {
  padding: 16px;
  border: 1px solid #fecaca;
  background: #fef2f2;
  color: #b91c1c;
  border-radius: 12px;
  font-size: 14px;
}
.public-kb-retry {
  margin-left: 12px;
  border: 1px solid #b91c1c;
  border-radius: 8px;
  background: transparent;
  color: #b91c1c;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
}
.dark .public-kb-retry {
  border-color: #fca5a5;
  color: #fca5a5;
}
.dark .public-kb-page-error {
  border-color: #7f1d1d;
  background: #450a0a;
  color: #fca5a5;
}
.public-kb-search {
  display: flex;
  gap: 10px;
}
.public-kb-search-input {
  flex: 1;
  border: 1px solid #cbd5e1;
  border-radius: 10px;
  padding: 10px 12px;
  font-size: 14px;
  background: #fff;
  color: #0f172a;
}
.dark .public-kb-search-input {
  border-color: #334155;
  background: #0f172a;
  color: #e2e8f0;
}
.public-kb-search-btn {
  border: 1px solid #2563eb;
  border-radius: 10px;
  background: #2563eb;
  color: #fff;
  padding: 10px 18px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.public-kb-search-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.public-kb-result-meta {
  margin: 18px 0 8px;
  font-size: 13px;
  color: #64748b;
}
.public-kb-result-hint {
  margin-top: 4px;
  font-size: 12px;
}
.public-kb-results {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.public-kb-result {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #fff;
  padding: 14px 16px;
}
.dark .public-kb-result {
  border-color: #1e293b;
  background: #0f172a;
}
.public-kb-result-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.public-kb-result-rank {
  font-size: 12px;
  font-weight: 700;
  color: #2563eb;
}
.dark .public-kb-result-rank {
  color: #93c5fd;
}
.public-kb-result-title {
  font-weight: 700;
  font-size: 15px;
}
.public-kb-result-score {
  margin-left: auto;
  font-size: 12px;
  color: #64748b;
  font-variant-numeric: tabular-nums;
}
.public-kb-result-snippet {
  margin-top: 6px;
  font-size: 13px;
  line-height: 1.6;
  color: #334155;
  white-space: pre-wrap;
}
.dark .public-kb-result-snippet {
  color: #cbd5e1;
}
.public-kb-page-footer {
  border-top: 1px solid #e2e8f0;
  padding: 14px 20px;
  text-align: center;
  font-size: 12px;
  color: #64748b;
}
.dark .public-kb-page-footer {
  border-color: #1e293b;
}
.public-kb-chat {
  margin-top: 30px;
  border-top: 1px solid #e2e8f0;
  padding-top: 20px;
}
.dark .public-kb-chat {
  border-color: #1e293b;
}
.public-kb-chat-title {
  font-size: 18px;
  font-weight: 800;
  color: #0f172a;
}
.dark .public-kb-chat-title {
  color: #e2e8f0;
}
.public-kb-chat-hint {
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}
.dark .public-kb-chat-hint {
  color: #94a3b8;
}
.public-kb-chat-log {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 14px;
}
.public-kb-chat-msg {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #fff;
  padding: 12px 14px;
}
.dark .public-kb-chat-msg {
  border-color: #1e293b;
  background: #0f172a;
}
.public-kb-chat-msg.is-user {
  border-color: #bfdbfe;
  background: #eff6ff;
}
.dark .public-kb-chat-msg.is-user {
  border-color: #1e40af;
  background: #0b1e3a;
}
.public-kb-chat-role {
  font-size: 12px;
  font-weight: 700;
  color: #2563eb;
}
.public-kb-chat-msg.is-assistant .public-kb-chat-role {
  color: #0f766e;
}
.dark .public-kb-chat-msg.is-assistant .public-kb-chat-role {
  color: #5eead4;
}
.public-kb-chat-content {
  margin-top: 6px;
  font-size: 14px;
  line-height: 1.7;
  color: #0f172a;
  white-space: pre-wrap;
}
.dark .public-kb-chat-content {
  color: #e2e8f0;
}
.public-kb-chat-pending {
  color: #64748b;
  font-style: italic;
}
.dark .public-kb-chat-pending {
  color: #94a3b8;
}
.public-kb-chat-sources {
  margin-top: 10px;
  border-top: 1px dashed #cbd5e1;
  padding-top: 8px;
}
.dark .public-kb-chat-sources {
  border-color: #334155;
}
.public-kb-chat-sources-title {
  font-size: 12px;
  font-weight: 700;
  color: #475569;
}
.dark .public-kb-chat-sources-title {
  color: #94a3b8;
}
.public-kb-chat-source-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 6px;
}
.public-kb-chat-source {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  background: #f8fafc;
}
.dark .public-kb-chat-source {
  border-color: #334155;
  background: #020617;
}
.public-kb-chat-form {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}
.public-kb-chat-stop {
  border: 1px solid #b91c1c;
  border-radius: 10px;
  background: #fff;
  color: #b91c1c;
  padding: 10px 18px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}
.dark .public-kb-chat-stop {
  background: #0f172a;
  color: #fca5a5;
  border-color: #fca5a5;
}
</style>
